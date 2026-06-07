"""
loss.py — BCE + Dice + EdgeLoss (Sobel)
========================================
仅包含损失函数，不含任何评估指标。
评估指标 (HD95) 放在 train.py 中计算。
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class DiceLoss(nn.Module):
    """标准 Dice Loss"""

    def __init__(self, smooth=1e-6):
        super().__init__()
        self.smooth = smooth

    def forward(self, pred, target):
        pred = pred.contiguous().view(-1)
        target = target.contiguous().view(-1)
        intersection = (pred * target).sum()
        union = pred.sum() + target.sum()
        return 1.0 - (2.0 * intersection + self.smooth) / (union + self.smooth)


class EdgeLoss(nn.Module):
    """
    边缘损失 — Sobel 提取边缘后计算 L1
    强制模型关注指甲边界区域的精度
    """

    def __init__(self):
        super().__init__()
        sobel_kernel_x = torch.tensor([[[[-1, 0, 1],
                                          [-2, 0, 2],
                                          [-1, 0, 1]]]], dtype=torch.float32)
        sobel_kernel_y = torch.tensor([[[[-1, -2, -1],
                                          [0, 0, 0],
                                          [1, 2, 1]]]], dtype=torch.float32)
        self.register_buffer("kernel_x", sobel_kernel_x)
        self.register_buffer("kernel_y", sobel_kernel_y)

    def _sobel(self, x):
        pad = F.pad(x, (1, 1, 1, 1), mode="reflect")
        gx = F.conv2d(pad, self.kernel_x)
        gy = F.conv2d(pad, self.kernel_y)
        return torch.sqrt(gx ** 2 + gy ** 2 + 1e-8)

    def forward(self, pred, target):
        edge_pred = self._sobel(pred)
        edge_target = self._sobel(target)
        return F.l1_loss(edge_pred, edge_target)


class CombinedLoss(nn.Module):
    """
    组合损失: BCE(0.3) + Dice(0.5) + EdgeLoss(0.2)
    - BCE 0.3: 保证像素级分类准确
    - Dice 0.5: 保证区域重叠度
    - Edge 0.2: 强制边缘贴合
    """

    def __init__(self, bce_weight=0.3, dice_weight=0.5, edge_weight=0.2):
        super().__init__()
        self.bce_weight = bce_weight
        self.dice_weight = dice_weight
        self.edge_weight = edge_weight

        self.bce = nn.BCELoss()
        self.dice = DiceLoss()
        self.edge = EdgeLoss()

    def forward(self, pred, target):
        loss_bce = self.bce(pred, target)
        loss_dice = self.dice(pred, target)
        loss_edge = self.edge(pred, target)

        return (self.bce_weight * loss_bce +
                self.dice_weight * loss_dice +
                self.edge_weight * loss_edge)


# ========== 评估指标 (不进loss, 仅验证阶段使用) ==========


def dice_iou(pred, target, smooth=1e-6):
    """计算 Dice 和 IoU"""
    pred_bin = (pred > 0.5).float()
    pred_b = pred_bin.contiguous().view(-1)
    target_b = target.contiguous().view(-1)

    intersection = (pred_b * target_b).sum().float()
    union = pred_b.sum() + target_b.sum()

    dice = (2.0 * intersection + smooth) / (union + smooth)
    iou = (intersection + smooth) / (target_b.sum() + pred_b.sum() - intersection + smooth)

    return dice.item(), iou.item()


def edge_dice_iou(pred, target, smooth=1e-6):
    """计算边缘区域的 Dice 和 IoU（Sobel 提取边缘后）"""
    sobel_k = torch.tensor([[[[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]]],
                           dtype=torch.float32, device=pred.device)

    def edge(m):
        pad = F.pad(m, (1, 1, 1, 1), mode="reflect")
        gx = F.conv2d(pad, sobel_k)
        gy = F.conv2d(pad, sobel_k.transpose(2, 3))
        return (gx ** 2 + gy ** 2 > 0.01).float()

    edge_pred = edge((pred > 0.5).float())
    edge_target = edge(target)

    inter = (edge_pred * edge_target).sum().float()
    total = edge_pred.sum() + edge_target.sum()

    ed = (2.0 * inter + smooth) / (total + smooth)
    ei = (inter + smooth) / (total - inter + smooth)

    return ed.item(), ei.item()