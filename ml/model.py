"""Compact 2-channel CNN for tornado-risk classification (Part B).

Small on purpose (~0.5M params) so inference is trivial on the kappa CPU later.
Input (2,128,128): channel 0 = reflectivity, channel 1 = storm-relative velocity.
Output: single logit (use BCEWithLogitsLoss / sigmoid).
"""
import torch.nn as nn


def _block(i, o):
    return nn.Sequential(
        nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


class TornadoCNN(nn.Module):
    def __init__(self, in_ch=2):
        super().__init__()
        self.features = nn.Sequential(
            _block(in_ch, 16), _block(16, 32), _block(32, 64), _block(64, 128),
        )
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(0.3), nn.Linear(128, 1),
        )

    def forward(self, x):
        return self.head(self.features(x)).squeeze(1)
