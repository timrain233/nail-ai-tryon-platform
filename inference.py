"""
inference.py — 单图推理: 原图 → mask → 叠加轮廓
==================================================
用法: python inference.py --image path/to/image.jpg [--model checkpoints/best_model.pth]
"""

import os
import cv2
import torch
import numpy as np
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def load_image(path: str, img_size: int = 512) -> tuple:
    """加载图片, 返回 (原图RGB, 归一化tensor)"""
    img_bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise FileNotFoundError(f"无法读取图片: {path}")
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = img_rgb.shape[:2]

    # resize 到模型输入尺寸
    img_resized = cv2.resize(img_rgb, (img_size, img_size), interpolation=cv2.INTER_LINEAR)
    normalized = img_resized.astype(np.float32) / 127.5 - 1.0
    tensor = torch.from_numpy(normalized).permute(2, 0, 1).unsqueeze(0).float()

    return img_rgb, tensor, (h, w)


def inference(model: torch.nn.Module, tensor: torch.Tensor, orig_size: tuple, device: torch.device):
    """推理, 返回与原图同尺寸的 mask"""
    model.eval()
    with torch.no_grad():
        tensor = tensor.to(device)
        pred = model(tensor).cpu().numpy()[0, 0]

    # resize 回原图尺寸
    mask = cv2.resize(pred, (orig_size[1], orig_size[0]), interpolation=cv2.INTER_LINEAR)
    mask_bin = (mask > 0.5).astype(np.uint8) * 255
    return mask_bin


def overlay_contour(img_rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """在原图上叠加红色轮廓"""
    overlay = img_rgb.copy()
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (255, 0, 0), 2)
    return overlay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=str, required=True, help="输入图片路径")
    parser.add_argument("--model", type=str, default=None, help="模型权重路径")
    parser.add_argument("--img_size", type=int, default=512)
    parser.add_argument("--output", type=str, default=None, help="输出路径 (可选)")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # 加载模型
    from model import UNet
    model_path = args.model or os.path.join(PROJECT_ROOT, "checkpoints", "best_model.pth")
    model = UNet(in_ch=3, out_ch=1)
    state = torch.load(model_path, map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.to(device)
    print(f"模型加载: {model_path}")

    # 推理
    img_rgb, tensor, orig_size = load_image(args.image, args.img_size)
    mask = inference(model, tensor, orig_size, device)
    overlay = overlay_contour(img_rgb, mask)

    # 输出
    out_dir = args.output or os.path.join(PROJECT_ROOT, "inference_output")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.image))[0]
    cv2.imwrite(os.path.join(out_dir, f"{base}_mask.png"), mask)
    cv2.imwrite(os.path.join(out_dir, f"{base}_overlay.png"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    print(f"输出: {out_dir}/")

    # 打印结果
    fg = cv2.countNonZero(mask)
    total = mask.shape[0] * mask.shape[1]
    print(f"指甲占比: {fg / total * 100:.1f}%")


if __name__ == "__main__":
    main()