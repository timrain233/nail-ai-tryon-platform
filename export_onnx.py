"""
export_onnx.py — TTA 推理 + 后处理 + ONNX 导出
================================================
用法:
  python export_onnx.py                    # 导出 ONNX
  python export_onnx.py --infer image.jpg  # 单图推理

特性:
  - TTA (horizontal flip + average)
  - 后处理: 阈值 → 最大连通域 → 形态学闭运算 → 轮廓平滑
  - 输出干净、无空洞、边缘光滑的 mask
"""

import os
import cv2
import numpy as np
import torch
import torch.onnx

from model import AttentionUNet

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_PATH = os.path.join(PROJECT_ROOT, "checkpoints", "best_model.pth")
ONNX_PATH = os.path.join(PROJECT_ROOT, "checkpoints", "nail_segment.onnx")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_model(checkpoint_path=CHECKPOINT_PATH, device=DEVICE):
    """加载 Attention UNet + 权重"""
    model = AttentionUNet(in_ch=3, out_ch=1, pretrained=False).to(device)
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=True)

    if "model_state_dict" in ckpt:
        model.load_state_dict(ckpt["model_state_dict"])
    else:
        model.load_state_dict(ckpt)

    model.eval()
    return model


# ========== TTA 推理 ==========


def tta_inference(model, img_tensor, device=DEVICE):
    """
    TTA (Test Time Augmentation)
    输入: (1, 3, H, W) torch.Tensor [-1, 1]
    返回: (1, 1, H, W) torch.Tensor [0, 1] (概率)
    """
    img_tensor = img_tensor.to(device)

    with torch.no_grad():
        pred_orig = model(img_tensor)
        pred_flip = model(torch.flip(img_tensor, dims=[3]))
        pred_flip = torch.flip(pred_flip, dims=[3])

        pred = (pred_orig + pred_flip) / 2.0

    return pred


# ========== 后处理管线 ==========


def postprocess(mask_prob, threshold=0.5, kernel_size=5, min_area_ratio=0.01):
    """
    后处理管线: 阈值 → 最大连通域 → 闭运算 → 轮廓平滑

    输入:
      mask_prob: (H, W) float32 [0,1] 概率图
      threshold: 二值化阈值
      kernel_size: 形态学核大小
      min_area_ratio: 最小面积占比 (过滤碎块)

    返回:
      mask_clean: (H, W) uint8 {0, 255} 干净掩码
    """
    # 1. 二值化
    binary = (mask_prob > threshold).astype(np.uint8)

    # 2. 形态学闭运算 (填充小空洞 + 平滑边缘)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    # 3. 再开运算 (去除小噪点)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)

    # 4. 提取最大连通域 (去除碎块)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)

    if num_labels <= 1:
        return np.zeros_like(binary) * 255

    # 找到最大连通域 (排除背景 label=0)
    areas = stats[1:, cv2.CC_STAT_AREA]
    max_idx = np.argmax(areas) + 1
    max_area = areas[max_idx - 1]
    img_area = binary.shape[0] * binary.shape[1]

    # 如果最大区域太小，返回空
    if max_area < img_area * min_area_ratio:
        return np.zeros_like(binary) * 255

    mask_largest = (labels == max_idx).astype(np.uint8)

    # 5. 轮廓平滑 (approxPolyDP 简化轮廓)
    contours, _ = cv2.findContours(mask_largest, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        eps = 0.001 * cv2.arcLength(contours[0], True)
        smoothed = cv2.approxPolyDP(contours[0], eps, True)
        mask_clean = np.zeros_like(mask_largest)
        cv2.fillPoly(mask_clean, [smoothed], 1)
        return (mask_clean * 255).astype(np.uint8)

    return (mask_largest * 255).astype(np.uint8)


def infer_single(model, image_path, img_size=512, device=DEVICE):
    """
    单图推理: 读图 → TTA → 后处理 → 返回干净 mask
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"无法读取图片: {image_path}")
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = img_rgb.shape[:2]

    # 预处理 ([-1,1] 标准化, 与 dataset.py 训练一致)
    img_resized = cv2.resize(img_rgb, (img_size, img_size)).astype(np.float32)
    img_norm = img_resized / 127.5 - 1.0
    img_tensor = torch.from_numpy(img_norm).permute(2, 0, 1).unsqueeze(0).float()

    # TTA
    pred_prob = tta_inference(model, img_tensor, device)  # (1,1,512,512)
    pred_prob = pred_prob.squeeze().cpu().numpy()  # (512,512)

    # 后处理
    mask_clean = postprocess(pred_prob)

    # 恢复原始尺寸
    if (h, w) != (img_size, img_size):
        mask_clean = cv2.resize(mask_clean, (w, h), interpolation=cv2.INTER_NEAREST)

    return mask_clean


# ========== ONNX 导出 ==========


def export_onnx(img_size=512, opset_version=12):
    """导出 ONNX，支持动态 batch 尺寸"""
    model = load_model()
    model.eval()

    dummy = torch.randn(1, 3, img_size, img_size, device=DEVICE)

    torch.onnx.export(
        model,
        dummy,
        ONNX_PATH,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={
            "input": {0: "batch", 2: "height", 3: "width"},
            "output": {0: "batch", 2: "height", 3: "width"},
        },
        opset_version=opset_version,
        do_constant_folding=True,
    )
    print(f"ONNX 已导出: {ONNX_PATH}")
    print(f"输入: (batch, 3, {img_size}, {img_size})")
    print(f"输出: (batch, 1, {img_size}, {img_size}) [0,1] sigmoid")

    # 验证
    import onnx
    onnx_model = onnx.load(ONNX_PATH)
    onnx.checker.check_model(onnx_model)
    print("ONNX 模型验证通过 ✅")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--infer", type=str, default=None, help="单图推理路径")
    parser.add_argument("--img_size", type=int, default=512)
    parser.add_argument("--opset", type=int, default=12)
    args = parser.parse_args()

    if args.infer:
        model = load_model()
        mask = infer_single(model, args.infer, img_size=args.img_size)
        out_path = args.infer.replace(".", "_mask.")
        cv2.imwrite(out_path, mask)
        print(f"Mask 已保存: {out_path}")
    else:
        export_onnx(img_size=args.img_size, opset_version=args.opset)