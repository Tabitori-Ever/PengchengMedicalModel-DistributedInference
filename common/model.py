import torch
import torch.nn as nn


class AlexNetPart1(nn.Module):
    """
    Part1：
        features
        avgpool
    输出：
        feature map
    """

    def __init__(self, full_model):
        super().__init__()

        self.features = full_model.features
        self.avgpool = full_model.avgpool

    def forward(self, x):

        x = self.features(x)

        x = self.avgpool(x)

        return x


class AlexNetPart2(nn.Module):
    """
    Part2：
        flatten
        classifier
    """

    def __init__(self, full_model):
        super().__init__()

        self.classifier = full_model.classifier

    def forward(self, x):

        x = torch.flatten(x, 1)

        x = self.classifier(x)

        return x