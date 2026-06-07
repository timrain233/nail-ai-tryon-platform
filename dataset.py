"""
dataset.py — Albumentations 强增强 + 8:2 划分
=============================================
数据路径: dataset/images/, dataset/masks/
"""

import os
import cv2
import numpy as np
import albumentations as A
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split


class NailDataset(Dataset):
    def __init__(self, image_paths, mask_paths, transform=None):
        self.image_paths = image_paths
        self.mask_paths = mask_paths
        self.transform = transform

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img = cv2.imread(self.image_paths[idx])
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        mask = cv2.imread(self.mask_paths[idx], cv2.IMREAD_GRAYSCALE)
        mask = (mask > 127).astype(np.float32)

        if self.transform:
            augmented = self.transform(image=img, mask=mask)
            img = augmented["image"]
            mask = augmented["mask"]

        # img: (3, H, W) float32, [-1, 1]
        # mask: (1, H, W) float32, [0, 1]
        img = img.astype(np.float32) / 127.5 - 1.0
        mask = mask[np.newaxis, ...]

        return img, mask


def get_training_augmentation(img_size=512):
    """训练增强 — 强增强保证泛化"""
    return A.Compose([
        # 几何增强
        A.HorizontalFlip(p=0.5),
        A.VerticalFlip(p=0.1),
        A.Rotate(limit=20, p=0.8, border_mode=cv2.BORDER_REFLECT),
        A.RandomScale(scale_limit=0.15, p=0.6),
        # 色彩光照增强
        A.RandomBrightnessContrast(
            brightness_limit=0.15, contrast_limit=0.15, p=0.8
        ),
        A.HueSaturationValue(
            hue_shift_limit=8, sat_shift_limit=15, val_shift_limit=15, p=0.5
        ),
        # 模糊/噪声（模拟光线散射、拍照模糊）
        A.GaussianBlur(blur_limit=(3, 5), p=0.3),
        A.GaussNoise(var_limit=(10.0, 30.0), p=0.2),
        # 弹性形变（模拟手指弯曲、皮肤拉伸）
        A.ElasticTransform(
            alpha=30, sigma=4, alpha_affine=10,
            border_mode=cv2.BORDER_REFLECT, p=0.3,
        ),
        # 网格变形/微透视（模拟不同拍摄角度）
        A.GridDistortion(
            num_steps=3, distort_limit=0.15,
            border_mode=cv2.BORDER_REFLECT, p=0.2,
        ),
        # 最终缩放到固定尺寸
        A.Resize(img_size, img_size),
    ])


def get_validation_transform(img_size=512):
    """验证增强 — 仅缩放"""
    return A.Compose([
        A.Resize(img_size, img_size),
    ])


def create_datasets(data_root, img_size=512, val_ratio=0.2, seed=42):
    """
    返回 (train_dataset, val_dataset)
    目录结构:
      data_root/
        images/   ← .jpg/.png
        masks/    ← .png (二值掩码)
    """
    img_dir = os.path.join(data_root, "images")
    mask_dir = os.path.join(data_root, "masks")

    if not os.path.isdir(img_dir) or not os.path.isdir(mask_dir):
        raise FileNotFoundError(
            f"数据目录不存在: {img_dir} 或 {mask_dir}\n"
            f"请确保 data_root 下包含 images/ 和 masks/ 子目录"
        )

    all_images = sorted([
        os.path.join(img_dir, f) for f in os.listdir(img_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ])
    all_masks = sorted([
        os.path.join(mask_dir, f) for f in os.listdir(mask_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ])

    # 按文件名前缀配对
    paired = []
    for img_p in all_images:
        basename = os.path.splitext(os.path.basename(img_p))[0]
        mask_candidates = [m for m in all_masks if basename in m]
        if mask_candidates:
            paired.append((img_p, mask_candidates[0]))

    if len(paired) == 0:
        raise ValueError("未找到配对的 image-mask 数据")

    images, masks = zip(*paired)
    images, masks = list(images), list(masks)

    train_imgs, val_imgs, train_masks, val_masks = train_test_split(
        images, masks, test_size=val_ratio, random_state=seed
    )

    train_ds = NailDataset(
        train_imgs, train_masks,
        transform=get_training_augmentation(img_size),
    )
    val_ds = NailDataset(
        val_imgs, val_masks,
        transform=get_validation_transform(img_size),
    )

    return train_ds, val_ds


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "./dataset"
    train_ds, val_ds = create_datasets(root, img_size=512)
    print(f"训练集: {len(train_ds)} 张")
    print(f"验证集: {len(val_ds)} 张")

    img, mask = train_ds[0]
    print(f"样本 shape: img={img.shape}, mask={mask.shape}")
    print(f"img range: [{img.min():.2f}, {img.max():.2f}]")
    print(f"mask range: [{mask.min():.2f}, {mask.max():.2f}]")