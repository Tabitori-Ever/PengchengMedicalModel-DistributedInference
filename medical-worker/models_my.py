import torch
import torch.nn as nn
import torch.nn.functional as F
import os
import numpy as np
from models.Sptmodel import SpectralTransform  # 假设 Sptmodel.py 已存在，否则需将其内容合并


# ========== 基础组件 ==========
class GELU(nn.Module):
    def forward(self, x):
        return F.gelu(x)


class CBAM2d(nn.Module):
    # 简化版 CBAM，可根据需要实现
    def __init__(self, channels, reduction=16):
        super(CBAM2d, self).__init__()
        self.channel_attention = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(channels, channels // reduction, 1),
            nn.ReLU(),
            nn.Conv2d(channels // reduction, channels, 1),
            nn.Sigmoid()
        )
        self.spatial_attention = nn.Sequential(
            nn.Conv2d(2, 1, kernel_size=7, padding=3),
            nn.Sigmoid()
        )

    def forward(self, x):
        ca = self.channel_attention(x)
        x = x * ca
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        spatial = torch.cat([avg_out, max_out], dim=1)
        sa = self.spatial_attention(spatial)
        return x * sa


class ResNetEncoder2d(nn.Module):
    """
    简单的 ResNet18 编码器，输出特征图（而不是全局向量）。
    这里简化实现，实际可用 torchvision.models.resnet18，去掉全连接层。
    """
    def __init__(self, in_channels=1):
        super().__init__()
        from torchvision.models import resnet18
        self.model = resnet18(pretrained=False)
        self.model.conv1 = nn.Conv2d(in_channels, 64, kernel_size=7, stride=2, padding=3, bias=False)
        # 移除最后的全局池化和 fc，只输出特征图
        self.features = nn.Sequential(*list(self.model.children())[:-2])

    def forward(self, x):
        return self.features(x)  # [B, 512, H/32, W/32] 对于 224 输入，输出 7x7


# ========== 模型组件 ==========
class GatedDynamicFusion(nn.Module):
    def __init__(self, in_channels):
        super(GatedDynamicFusion, self).__init__()
        self.gate_conv = nn.Sequential(
            nn.Conv2d(in_channels * 2, in_channels, kernel_size=1),
            nn.Sigmoid()
        )
        self.fusion_conv = nn.Sequential(
            nn.Conv2d(in_channels * 2, in_channels, kernel_size=3, padding=1),
            GELU()
        )

    def forward(self, dce_features, dwi_features):
        combined = torch.cat([dce_features, dwi_features], dim=1)
        dce_gate = self.gate_conv(combined)
        dwi_gate = 1 - dce_gate
        dce_gated = dce_features * dce_gate
        dwi_gated = dwi_features * dwi_gate
        fused = self.fusion_conv(torch.cat([dce_gated, dwi_gated], dim=1))
        return fused


class MedicalImageProcessor(nn.Module):
    def __init__(self, in_channel=1, d_model=256, dropout=0.25, weights_path=None, device=torch.device("cuda")):
        super(MedicalImageProcessor, self).__init__()
        self.device = device
        self.weights_path = weights_path

        self.encoder_dce = ResNetEncoder2d(in_channels=in_channel)
        self.encoder_dwi = ResNetEncoder2d(in_channels=in_channel)

        # 降维卷积，将 512 通道降到 d_model
        self.down_dce = nn.Conv2d(512, d_model, kernel_size=1)
        self.down_dwi = nn.Conv2d(512, d_model, kernel_size=1)

        if weights_path:
            self._load_and_freeze_weights()

        self.fusion_module = GatedDynamicFusion(in_channels=d_model)
        self.spectral_fusion = SpectralTransform(in_channels=d_model, out_channels=d_model)
        self.final_fusion_conv = nn.Sequential(
            nn.Conv2d(d_model * 2, d_model, kernel_size=1),
            GELU()
        )
        self.global_pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, pre_dce, pre_dwi):
        dce_feat = self.encoder_dce(pre_dce)  # [B, 512, H, W]
        dwi_feat = self.encoder_dwi(pre_dwi)  # [B, 512, H, W]

        # 降维到 d_model
        dce_feat = self.down_dce(dce_feat)  # [B, d_model, H, W]
        dwi_feat = self.down_dwi(dwi_feat)  # [B, d_model, H, W]

        fused = self.fusion_module(dce_feat, dwi_feat)
        fused_features_spt = self.spectral_fusion(dce_feat, dwi_feat)
        combined = torch.cat([fused, fused_features_spt], dim=1)
        final = self.final_fusion_conv(combined)
        token = self.global_pool(final).squeeze()
        if token.dim() == 1:
            token = token.unsqueeze(0)
        return token


class ClinicalProcessor(nn.Module):
    def __init__(self, input_dim, d_model=256, dropout=0.25):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(input_dim, d_model),
            nn.LayerNorm(d_model),
            nn.ReLU(),
            nn.Dropout(dropout)
        )

    def forward(self, x):
        return self.fc(x)


class RadiomicsProcessor(nn.Module):
    def __init__(self, input_dim, d_model=256, dropout=0.25):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(input_dim, d_model),
            nn.LayerNorm(d_model),
            nn.ReLU(),
            nn.Dropout(dropout)
        )

    def forward(self, x):
        return self.fc(x)


class CrossModalAttention(nn.Module):
    def __init__(self, d_model=256):
        super().__init__()
        self.attn = nn.MultiheadAttention(d_model, num_heads=4, batch_first=True)

    def forward(self, query, key):
        # query, key: [B, d_model]
        query = query.unsqueeze(1)  # [B, 1, d_model]
        key = key.unsqueeze(1)
        attn_out, _ = self.attn(query, key, key)
        return attn_out.squeeze(1)


class CMFA(nn.Module):
    def __init__(self, img_dim=256, tab_dim=256, hid_dim=256, heads=4, dropout=0.25):
        super().__init__()
        self.img_proj = nn.Linear(img_dim, hid_dim)
        self.tab_proj = nn.Linear(tab_dim, hid_dim)
        self.attn = nn.MultiheadAttention(hid_dim, heads, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(hid_dim)
        self.fc_out = nn.Linear(hid_dim, hid_dim)

    def forward(self, img, tab):
        img = self.img_proj(img).unsqueeze(1)
        tab = self.tab_proj(tab).unsqueeze(1)
        attn_out, _ = self.attn(img, tab, tab)
        out = self.norm(attn_out + img)
        out = self.fc_out(out).squeeze(1)
        return out


class DynamicFusion(nn.Module):
    def __init__(self, d_model=256):
        super().__init__()
        self.gate = nn.Sequential(
            nn.Linear(d_model * 2, d_model),
            nn.Sigmoid()
        )

    def forward(self, a, b):
        combined = torch.cat([a, b], dim=-1)
        alpha = self.gate(combined)
        return alpha * a + (1 - alpha) * b


class GatedVectorFusion(nn.Module):
    def __init__(self, d_model=256):
        super().__init__()
        self.gate = nn.Sequential(
            nn.Linear(d_model * 2, d_model),
            nn.Sigmoid()
        )

    def forward(self, img_token, tab_token):
        combined = torch.cat([img_token, tab_token], dim=-1)
        gate_weights = self.gate(combined)
        return img_token * gate_weights + tab_token * (1 - gate_weights)


class FocalLoss(nn.Module):
    def __init__(self, class_num, alpha=None, gamma=2.0, reduction='mean'):
        super().__init__()
        if alpha is None:
            self.alpha = torch.ones(class_num)
        else:
            self.alpha = torch.tensor(alpha)
        self.gamma = gamma
        self.reduction = reduction

    def forward(self, inputs, targets):
        ce_loss = F.cross_entropy(inputs, targets, reduction='none')
        pt = torch.exp(-ce_loss)
        # 关键修复：将 self.alpha 移动到与 inputs 相同的设备
        alpha = self.alpha.to(inputs.device)
        alpha_t = alpha[targets]
        focal_loss = alpha_t * (1 - pt) ** self.gamma * ce_loss
        if self.reduction == 'mean':
            return focal_loss.mean()
        elif self.reduction == 'sum':
            return focal_loss.sum()
        else:
            return focal_loss


class DoubleTower(nn.Module):
    def __init__(self, in_channel=1, clinical_dim=23, rad_dim=2264, d_model=256,
                 dropout_rate=0.25, num_classes=2, weights_path=None,
                 focal_alpha=None, focal_gamma=2.0, device=torch.device("cuda")):
        super().__init__()
        self.device = device
        self.image_processor = MedicalImageProcessor(
            in_channel=in_channel, d_model=d_model, dropout=dropout_rate,
            weights_path=weights_path, device=device
        )
        self.rad_processor = RadiomicsProcessor(input_dim=rad_dim, d_model=d_model, dropout=dropout_rate)
        self.clin_processor = ClinicalProcessor(input_dim=clinical_dim, d_model=d_model, dropout=dropout_rate)

        self.cross_attn = CrossModalAttention(d_model=d_model)
        self.cmfa = CMFA(img_dim=d_model, tab_dim=d_model, hid_dim=d_model, heads=4, dropout=dropout_rate)
        self.fusion = DynamicFusion(d_model=d_model)
        self.gated_fusion = GatedVectorFusion(d_model=d_model)

        # ========== 修改点：第一层输入维度从 d_model*2 改为 d_model ==========
        self.classifier = nn.Sequential(
            nn.Linear(d_model, 256),          # 原为 d_model*2
            nn.LayerNorm(256),
            nn.ReLU(inplace=False),
            nn.Dropout(dropout_rate),
            nn.Linear(256, 128),
            nn.LayerNorm(128),
            nn.ReLU(inplace=False),
            nn.Dropout(dropout_rate),
            nn.Linear(128, num_classes)
        ).to(device)

        self.focal_loss = FocalLoss(
            class_num=num_classes,
            alpha=focal_alpha,
            gamma=focal_gamma
        )

    def forward(self, x1, x2, x_clin, x_rad, label=None):
        token_img = self.image_processor(x1, x2)
        token_rad = self.rad_processor(x_rad)
        token_clin = self.clin_processor(x_clin)

        attn_enhanced = self.cross_attn(token_clin, token_rad)
        token_rad_clin = self.fusion(token_clin, attn_enhanced)

        tokens = self.cmfa(token_img, token_rad_clin)

        logits = self.classifier(tokens)

        if label is not None:
            fl = self.focal_loss(logits, label)
            return logits, fl
        return logits