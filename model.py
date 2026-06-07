"""
model.py — Attention UNet (ResNet34 预训练编码器)
==================================================
输入: (B, 3, H, W)  输出: (B, 1, H, W)  [0,1] sigmoid
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models


class ResNet34Encoder(nn.Module):
    """ResNet34 分阶段提取特征，输出 4 层 skip + 1 层 bottleneck"""

    def __init__(self, pretrained=True):
        super().__init__()
        resnet = models.resnet34(
            weights=models.ResNet34_Weights.IMAGENET1K_V1 if pretrained else None
        )

        # 按阶段拆分
        self.stem = nn.Sequential(
            resnet.conv1,
            resnet.bn1,
            resnet.relu,
        )  # 1/2,  64
        self.pool = resnet.maxpool  # 1/4, 64

        self.layer1 = resnet.layer1  # 1/4,  64
        self.layer2 = resnet.layer2  # 1/8,  128
        self.layer3 = resnet.layer3  # 1/16, 256
        self.layer4 = resnet.layer4  # 1/32, 512

        self.skip_channels = [64, 64, 128, 256]
        self.bottleneck_channels = 512

    def forward(self, x):
        s0 = self.stem(x)       # 1/2
        p = self.pool(s0)       # 1/4
        s1 = self.layer1(p)     # 1/4
        s2 = self.layer2(s1)    # 1/8
        s3 = self.layer3(s2)    # 1/16
        b = self.layer4(s3)     # 1/32
        return [s0, s1, s2, s3], b


class ConvBnRelu(nn.Module):
    def __init__(self, in_ch, out_ch, kernel=3, padding=1):
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, kernel, padding=padding, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.block(x)


class AttentionGate(nn.Module):
    """
    Attention Gate (AG) 来自 Attention U-Net 论文
    f_g: gating signal from decoder
    f_x: skip connection from encoder
    """

    def __init__(self, f_g_ch, f_x_ch, inter_ch=None):
        super().__init__()
        if inter_ch is None:
            inter_ch = f_g_ch // 2 if f_g_ch >= 32 else f_g_ch
        self.w_g = nn.Conv2d(f_g_ch, inter_ch, kernel_size=1, bias=False)
        self.w_x = nn.Conv2d(f_x_ch, inter_ch, kernel_size=1, bias=False)
        self.psi = nn.Sequential(
            nn.ReLU(inplace=True),
            nn.Conv2d(inter_ch, 1, kernel_size=1, bias=False),
            nn.Sigmoid(),
        )

    def forward(self, g, x):
        g1 = self.w_g(g)
        x1 = self.w_x(x)
        if g1.shape[2:] != x1.shape[2:]:
            x1 = F.interpolate(x1, size=g1.shape[2:], mode="bilinear", align_corners=False)
        psi = self.psi(g1 + x1)
        if psi.shape[2:] != x.shape[2:]:
            psi = F.interpolate(psi, size=x.shape[2:], mode="bilinear", align_corners=False)
        return x * psi


class DecoderBlock(nn.Module):
    def __init__(self, in_ch, skip_ch, out_ch):
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.conv1 = ConvBnRelu(in_ch + skip_ch, out_ch)
        self.conv2 = ConvBnRelu(out_ch, out_ch)
        self.attn = AttentionGate(f_g_ch=in_ch, f_x_ch=skip_ch)

    def forward(self, x, skip):
        x = self.up(x)
        skip = self.attn(x, skip)
        # 处理尺寸对齐
        if x.shape[2:] != skip.shape[2:]:
            diff_h = skip.shape[2] - x.shape[2]
            diff_w = skip.shape[3] - x.shape[3]
            x = F.pad(x, [diff_w // 2, diff_w - diff_w // 2,
                          diff_h // 2, diff_h - diff_h // 2])
        x = torch.cat([x, skip], dim=1)
        x = self.conv1(x)
        x = self.conv2(x)
        return x


class AttentionUNet(nn.Module):
    """
    Attention U-Net with ResNet34 encoder

    输入: (B, 3, H, W)  推荐 H,W 为 32 的倍数
    输出: (B, 1, H, W)  已通过 Sigmoid 归一化到 [0,1]
    """

    def __init__(self, in_ch=3, out_ch=1, base_ch=64, pretrained=True):
        super().__init__()
        self.encoder = ResNet34Encoder(pretrained=pretrained)

        skip_chs = self.encoder.skip_channels
        bot_ch = self.encoder.bottleneck_channels

        # Bridge
        self.bridge = ConvBnRelu(bot_ch, bot_ch)

        # Decoder
        self.dec4 = DecoderBlock(bot_ch, skip_chs[3], 256)
        self.dec3 = DecoderBlock(256, skip_chs[2], 128)
        self.dec2 = DecoderBlock(128, skip_chs[1], 64)
        self.dec1 = DecoderBlock(64, skip_chs[0], 64)

        # Upsample to original resolution (1/2 → 1/1)
        self.final_up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.final_conv = nn.Sequential(
            ConvBnRelu(64, 32),
            nn.Conv2d(32, out_ch, kernel_size=1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        skips, bottleneck = self.encoder(x)

        b = self.bridge(bottleneck)  # 1/32

        d4 = self.dec4(b, skips[3])   # 1/16
        d3 = self.dec3(d4, skips[2])  # 1/8
        d2 = self.dec2(d3, skips[1])  # 1/4
        d1 = self.dec1(d2, skips[0])  # 1/2

        out = self.final_up(d1)        # 1/1
        out = self.final_conv(out)
        return out


def count_params(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


if __name__ == "__main__":
    model = AttentionUNet(pretrained=False)
    x = torch.randn(2, 3, 512, 512)
    y = model(x)
    print(f"输入: {x.shape}")
    print(f"输出: {y.shape}")
    print(f"参数量: {count_params(model) / 1e6:.2f}M")