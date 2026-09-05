import os
import json
import random
import numpy as np
import pandas as pd
from PIL import Image
import nibabel as nib
import torch
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
import torchvision.transforms.functional as F
from sklearn.preprocessing import StandardScaler, OrdinalEncoder, OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline


def load_nifti_as_pil(file_path):
    """
    加载 .nii.gz 文件，如果是 3D 则取中间切片，转换为灰度 PIL Image。
    """
    img_nib = nib.load(file_path)
    data = img_nib.get_fdata()  # shape: (H, W) 或 (H, W, D)
    if data.ndim == 3:
        # 取中间切片 (假设 D 是切片数)
        mid_slice = data.shape[2] // 2
        data = data[:, :, mid_slice]
    # 归一化到 0-255 范围
    data_min, data_max = data.min(), data.max()
    if data_max > data_min:
        data = (data - data_min) / (data_max - data_min) * 255
    else:
        data = np.zeros_like(data)
    data = data.astype(np.uint8)
    return Image.fromarray(data, mode='L')


def read_split_data_by_hospital(
    csv_file_path: str,
    rad_file_path: str,
    image_root: str = None,                     # 可选：单一根目录（用于向后兼容）
    train_hospitals: list = [4, 1, 5],          # 修改默认训练医院为 [4,1,5]
    random_seed: int = 0,
    hospital_root_map: dict = None              # 新增：医院ID -> 图像根目录的映射
):
    """
    读取数据，按医院划分训练集（包括内部验证）和外部测试集。
    参数：
        hospital_root_map: dict，例如 {1: "/path/to/hospital1", 2: "/path/to/hospital2", ...}
                          如果提供，则根据患者医院ID选择对应根目录；
                          否则使用 image_root 作为统一根目录。
    返回：
        train_val_data: list of dict, 用于交叉验证的训练/验证池
        dict_of_test_sets: dict {hospital_id: list of dict}
    """
    random.seed(random_seed)
    np.random.seed(random_seed)

    assert os.path.exists(csv_file_path), f"CSV file: {csv_file_path} does not exist."
    assert os.path.exists(rad_file_path), f"Radiomics CSV file: {rad_file_path} does not exist."

    # 检查图像根目录
    if hospital_root_map is None:
        assert image_root is not None and os.path.exists(image_root), f"Image root directory: {image_root} does not exist."
    else:
        for hid, root in hospital_root_map.items():
            assert os.path.exists(root), f"Image root for hospital {hid}: {root} does not exist."

    # --- 1. 加载并初步处理原始临床数据 ---
    df_raw = pd.read_csv(csv_file_path, encoding='gbk')
    df_raw['patient_ID'] = df_raw['patient_ID'].astype(str).str.strip()
    df_raw['bpCR'] = df_raw['bpCR'].astype(int)
    df_raw['hospital'] = df_raw['hospital'].astype(int)

    # --- 2. 加载并标准化放射组学数据 ---
    df_rad = pd.read_csv(rad_file_path, encoding='gbk')
    df_rad['patient_ID'] = df_rad['patient_ID'].astype(str).str.strip()
    if 'hospital' in df_rad.columns:
        df_rad['hospital'] = df_rad['hospital'].astype(int)
    else:
        raise ValueError("Radiomics CSV file must contain a 'hospital' column for merging.")

    # 提取放射组学特征列
    rad_cols = [col for col in df_rad.columns if any(x in col for x in ['preDCE_', 'preDWI_'])]

    # Z-score 标准化
    scaler = StandardScaler()
    df_rad[rad_cols] = scaler.fit_transform(df_rad[rad_cols])

    # --- 3. 合并临床数据和放射组学特征 ---
    df_merged = pd.merge(
        df_raw,
        df_rad[['patient_ID', 'hospital'] + rad_cols],
        on=['patient_ID', 'hospital'],
        how='left'
    )

    # --- 4. 定义临床特征列并初始化预处理器 ---
    clinical_cols = [
        'T_stage', 'HER2_status', 'NAC_classification', 'ER_status', 'PR_status',
        'Ki_67', 'age', 'age_menarche', 'BMI', 'treatment_duration', 'NAC_treatment_cycle',
        'endocrine', 'Histological_type', 'result_ipsilateral_axillary_LNP', 'N_stage',
        'M_stage', 'enlargement_unilateral_axillary_LN', 'enlargement_unilateral_clavicular_LN',
        'enlargement_Ipsilateral_internal_mammary_LN', 'target_lesion_location', 'target_lesion_quadrant'
    ]

    numeric_features = ['Ki_67', 'age', 'age_menarche', 'BMI']
    ordinal_features = ['HER2_status', 'T_stage', 'N_stage', 'M_stage']
    nominal_features = ['PR_status', 'ER_status']
    raw_features = [
        'NAC_classification', 'treatment_duration', 'NAC_treatment_cycle', 'endocrine',
        'Histological_type', 'result_ipsilateral_axillary_LNP', 'enlargement_unilateral_axillary_LN',
        'enlargement_unilateral_clavicular_LN', 'enlargement_Ipsilateral_internal_mammary_LN',
        'target_lesion_location', 'target_lesion_quadrant'
    ]

    preprocessor = ColumnTransformer(transformers=[
        ('num', Pipeline([
            ('imputer', SimpleImputer(strategy='mean')),
            ('scaler', StandardScaler())
        ]), numeric_features),
        ('ordinal', Pipeline([
            ('imputer', SimpleImputer(strategy='most_frequent')),
            ('encoder', OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=-1))
        ]), ordinal_features),
        ('nominal', Pipeline([
            ('imputer', SimpleImputer(strategy='most_frequent')),
            ('encoder', OneHotEncoder(handle_unknown='ignore'))
        ]), nominal_features),
        ('raw', 'passthrough', raw_features)
    ])

    preprocessor.fit(df_merged[clinical_cols])

    # --- 5. 构建患者信息列表 ---
    all_patient_info = []
    class_labels = sorted(df_merged['bpCR'].unique().tolist())
    class_indices = dict((k, v) for v, k in enumerate(class_labels))
    print(f"Class indices: {json.dumps(dict((val, key) for key, val in class_indices.items()), indent=4)}")

    for idx, row in df_merged.iterrows():
        patient_id = str(row['patient_ID'])
        hospital_id = int(row['hospital'])
        label = int(row['bpCR'])

        # 根据医院ID选择根目录
        if hospital_root_map is not None:
            root_dir = hospital_root_map.get(hospital_id)
            if root_dir is None:
                print(f"Warning: No root directory defined for hospital {hospital_id}, skipping patient {patient_id}")
                continue
        else:
            root_dir = image_root

        pre_dwi_path = os.path.join(root_dir, f"{patient_id}_dwi1_roi.nii.gz")
        pre_dce_path = os.path.join(root_dir, f"{patient_id}_c1_roi.nii.gz")

        if not (os.path.exists(pre_dwi_path) and os.path.exists(pre_dce_path)):
            missing = []
            if not os.path.exists(pre_dwi_path):
                missing.append(f"{patient_id}_dwi1_roi.nii.gz")
            if not os.path.exists(pre_dce_path):
                missing.append(f"{patient_id}_c1_roi.nii.gz")
            print(f"Warning: Missing MRI sequences for {patient_id}. Skipping. Missing: {', '.join(missing)}")
            continue

        # 临床特征预处理
        clinical_data_single_row = row[clinical_cols].to_frame().T
        processed_clinical = preprocessor.transform(clinical_data_single_row)
        if hasattr(processed_clinical, 'toarray'):
            processed_clinical = processed_clinical.toarray().flatten()
        else:
            processed_clinical = processed_clinical.flatten()

        # 放射组学特征
        radiomics_features = row[rad_cols].values.astype(np.float32)

        all_patient_info.append({
            'patient_id': patient_id,
            'label': label,
            'hospital': hospital_id,
            'pre_dwi': pre_dwi_path,
            'pre_dce': pre_dce_path,
            'clinical': processed_clinical.astype(np.float32),
            'radiomics': radiomics_features.astype(np.float32)
        })

    if not all_patient_info:
        raise ValueError("No complete patient data found.")

    all_patient_df_processed = pd.DataFrame(all_patient_info)

    # 划分训练/验证池和外部测试集
    train_val_data = all_patient_df_processed[all_patient_df_processed['hospital'].isin(train_hospitals)].to_dict('records')
    external_test_data = all_patient_df_processed[~all_patient_df_processed['hospital'].isin(train_hospitals)]

    dict_of_test_sets = {}
    test_hospital_ids = sorted(external_test_data['hospital'].unique().tolist())
    for hospital_id in test_hospital_ids:
        dict_of_test_sets[hospital_id] = external_test_data[external_test_data['hospital'] == hospital_id].to_dict('records')

    print(f"\n--- Dataset Split Statistics ---")
    print(f"Total complete patients: {len(all_patient_info)}")
    print(f"Train/Validation pool: {len(train_val_data)} (Hospitals: {train_hospitals})")
    print("\n--- External Test Sets ---")
    for hid, data in dict_of_test_sets.items():
        print(f"   Hospital {hid}: {len(data)} patients")
        if data:
            labels = [d['label'] for d in data]
            print(f"     Class distribution: {pd.Series(labels).value_counts().sort_index().to_dict()}")

    return train_val_data, dict_of_test_sets


class MyDataSet(Dataset):
    def __init__(self, patient_data_list, transform=None):
        self.patient_data_list = patient_data_list
        self.transform = transform

    def __len__(self):
        return len(self.patient_data_list)

    def __getitem__(self, idx):
        info = self.patient_data_list[idx]

        # 使用 nibabel 加载 NIfTI 文件并转换为 PIL Image
        pre_dwi = load_nifti_as_pil(info['pre_dwi'])
        pre_dce = load_nifti_as_pil(info['pre_dce'])

        label = info['label']
        patient_id = info['patient_id']
        clinical = info['clinical']
        radiomics = info['radiomics']

        if self.transform is not None:
            if isinstance(self.transform, PairedTransforms):
                pre_dwi, pre_dce = self.transform(pre_dwi, pre_dce)
            elif isinstance(self.transform, transforms.Compose):
                pre_dwi = self.transform(pre_dwi)
                pre_dce = self.transform(pre_dce)

        return (pre_dwi, pre_dce,
                torch.from_numpy(clinical),
                torch.from_numpy(radiomics),
                label, patient_id)

    @staticmethod
    def collate_fn(batch):
        pre_dwi, pre_dce, clinical, radiomics, labels, patient_ids = zip(*batch)
        return {
            'pre_dwi': torch.stack(pre_dwi, 0),
            'pre_dce': torch.stack(pre_dce, 0),
            'clinical': torch.stack(clinical, 0),
            'radiomics': torch.stack(radiomics, 0),
            'label': torch.as_tensor(labels, dtype=torch.long),
            'patient_ids': patient_ids
        }


class PairedTransforms:
    def __init__(self, random_transforms, final_transforms):
        self.random_transforms = random_transforms
        self.final_transforms = final_transforms

    def __call__(self, img_pre_dwi, img_pre_dce):
        seed = random.randint(0, 100000)

        # 特殊处理 RandomResizedCrop
        transform = self.random_transforms[0]
        i, j, h, w = transform.get_params(img_pre_dwi, transform.scale, transform.ratio)
        img_pre_dwi = F.resized_crop(img_pre_dwi, i, j, h, w, transform.size, transform.interpolation)
        img_pre_dce = F.resized_crop(img_pre_dce, i, j, h, w, transform.size, transform.interpolation)

        for transform in self.random_transforms[1:]:
            torch.manual_seed(seed)
            img_pre_dwi = transform(img_pre_dwi)
            torch.manual_seed(seed)
            img_pre_dce = transform(img_pre_dce)

        img_pre_dwi = self.final_transforms(img_pre_dwi)
        img_pre_dce = self.final_transforms(img_pre_dce)
        return img_pre_dwi, img_pre_dce


def get_train_transforms(img_size=224):
    train_transforms = [
        transforms.RandomResizedCrop(img_size, scale=(0.8, 1.0), ratio=(3./4, 4./3)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(),
        transforms.RandomRotation(degrees=15),
        transforms.RandomAffine(degrees=0, translate=(0.1, 0.1), shear=0)
    ]
    final_transforms = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5], std=[0.5])
    ])
    return PairedTransforms(train_transforms, final_transforms)


def get_test_transforms(img_size=224):
    return transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(img_size),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5], std=[0.5])
    ])