"""
分析 cut_nail_png 目录下指定 PNG 文件的 RGBA 信息和视觉质量。
不修改任何文件，仅做分析报告。
"""
import sys
import os
import numpy as np
from PIL import Image, ImageFilter


FILES = [
    r"assets\cut_nail_png\img_008\2.png",
    r"assets\cut_nail_png\img_008\4.png",
    r"assets\cut_nail_png\img_009\4.png",
    r"assets\cut_nail_png\img_010\3.png",
    r"assets\cut_nail_png\img_010\4.png",
    r"assets\cut_nail_png\img_011\0.png",
    r"assets\cut_nail_png\img_011\2.png",
    r"assets\cut_nail_png\img_020\0.png",
    r"assets\cut_nail_png\img_020\1.png",
    r"assets\cut_nail_png\img_020\2.png",
    r"assets\cut_nail_png\img_020\3.png",
    r"assets\cut_nail_png\img_020\4.png",
]

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE_DIR = _PROJ_ROOT


def analyze_alpha_quality(alpha: np.ndarray) -> dict:
    """分析 alpha 通道的质量指标"""
    h, w = alpha.shape
    total = h * w
    zero_mask = alpha == 0
    nonzero_mask = alpha > 0
    zero_count = int(np.sum(zero_mask))
    nonzero_count = int(np.sum(nonzero_mask))
    nonzero_pct = 100.0 * nonzero_count / total if total > 0 else 0.0
    
    # 半透明像素 (0 < alpha < 255)
    semi_count = int(np.sum((alpha > 0) & (alpha < 255)))
    semi_pct = 100.0 * semi_count / total if total > 0 else 0.0
    
    # 完全 opaque 像素
    opaque_count = int(np.sum(alpha == 255))
    opaque_pct = 100.0 * opaque_count / total if total > 0 else 0.0
    
    return {
        "shape": (h, w),
        "total_pixels": total,
        "zero_alpha_pixels": zero_count,
        "nonzero_alpha_pixels": nonzero_count,
        "nonzero_alpha_pct": round(nonzero_pct, 2),
        "semi_transparent_pixels": semi_count,
        "semi_transparent_pct": round(semi_pct, 2),
        "opaque_pixels": opaque_count,
        "opaque_pct": round(opaque_pct, 2),
    }


def detect_edge_issues(rgba: np.ndarray) -> dict:
    """检测 mask 边缘质量问题"""
    alpha = rgba[:, :, 3]
    h, w = alpha.shape
    
    # 1. 检查是否有孤立的透明孔洞 (在非透明区域内的小透明点)
    # 对 alpha 做二值化
    binary = (alpha > 0).astype(np.uint8) * 255
    
    # 2. 检查边界平滑度: 计算 alpha 的梯度
    grad_y = np.abs(np.diff(alpha.astype(float), axis=0))
    grad_x = np.abs(np.diff(alpha.astype(float), axis=1))
    
    # 边缘像素: 在非全透明/非全不透明边界的梯度
    edge_gradients = []
    if grad_y.size > 0:
        edge_gradients.append(np.mean(grad_y))
    if grad_x.size > 0:
        edge_gradients.append(np.mean(grad_x))
    
    avg_edge_gradient = float(np.mean(edge_gradients)) if edge_gradients else 0.0
    
    # 3. 检查 alpha 通道的最小值和最大值 (看有没有"切得太死"的问题)
    nonzero_alphas = alpha[alpha > 0]
    min_alpha = int(np.min(nonzero_alphas)) if len(nonzero_alphas) > 0 else 0
    max_alpha = int(np.max(alpha))
    mean_alpha = float(np.mean(nonzero_alphas)) if len(nonzero_alphas) > 0 else 0.0
    
    # 4. 检查边界是否有锯齿状的硬边
    # 统计在边界区域的 alpha 值分布 (在mask边缘2px范围内)
    from scipy.ndimage import binary_dilation, binary_erosion
    
    try:
        mask = alpha > 0
        eroded = binary_erosion(mask, iterations=2)
        dilated = binary_dilation(mask, iterations=2)
        border_region = dilated & ~eroded
        border_alphas = alpha[border_region]
        border_mean = float(np.mean(border_alphas)) if len(border_alphas) > 0 else 0.0
        border_std = float(np.std(border_alphas)) if len(border_alphas) > 0 else 0.0
    except Exception:
        border_mean = -1
        border_std = -1
    
    return {
        "avg_edge_gradient": round(avg_edge_gradient, 2),
        "min_alpha_nonzero": min_alpha,
        "max_alpha": max_alpha,
        "mean_alpha_nonzero": round(mean_alpha, 1),
        "border_alpha_mean": round(border_mean, 1),
        "border_alpha_std": round(border_std, 2),
    }


def analyze_file(filepath: str):
    """分析单个 PNG 文件"""
    abs_path = os.path.join(BASE_DIR, filepath)
    if not os.path.exists(abs_path):
        print(f"\n{'='*70}")
        print(f"❌ 文件不存在: {filepath}")
        print(f"{'='*70}")
        return
    
    try:
        img = Image.open(abs_path).convert("RGBA")
        rgba = np.array(img)
        
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            print(f"\n{'='*70}")
            print(f"⚠️  文件不是 RGBA 格式: {filepath}, shape={rgba.shape}")
            print(f"{'='*70}")
            return
        
        alpha_info = analyze_alpha_quality(rgba[:, :, 3])
        edge_info = detect_edge_issues(rgba)
        
        has_zero_alpha = alpha_info["zero_alpha_pixels"] > 0
        
        print(f"\n{'='*70}")
        print(f"📄 文件: {filepath}")
        print(f"{'='*70}")
        print(f"  图像尺寸 (H x W): {alpha_info['shape']}")
        print(f"  总像素数:         {alpha_info['total_pixels']}")
        print(f"  ── Alpha 通道统计 ──")
        print(f"  是否有零值像素:    {'✅ 是 (有透明区域)' if has_zero_alpha else '❌ 否 (全不透明)'}")
        print(f"  零值(透明)像素:    {alpha_info['zero_alpha_pixels']} ({100 - alpha_info['nonzero_alpha_pct']:.2f}%)")
        print(f"  非零 Alpha 像素:   {alpha_info['nonzero_alpha_pixels']} ({alpha_info['nonzero_alpha_pct']}%)")
        print(f"  完全不透明像素:    {alpha_info['opaque_pixels']} ({alpha_info['opaque_pct']}%)")
        print(f"  半透明像素:        {alpha_info['semi_transparent_pixels']} ({alpha_info['semi_transparent_pct']}%)")
        print(f"  ── 边缘质量指标 ──")
        print(f"  平均边缘梯度:      {edge_info['avg_edge_gradient']} (越低越平滑)")
        print(f"  非零 Alpha 最小值: {edge_info['min_alpha_nonzero']}")
        print(f"  Alpha 最大值:      {edge_info['max_alpha']}")
        print(f"  非零 Alpha 均值:   {edge_info['mean_alpha_nonzero']}")
        print(f"  边界区域 Alpha 均值: {edge_info['border_alpha_mean']}")
        print(f"  边界区域 Alpha 标准差: {edge_info['border_alpha_std']} (越低越整齐)")
        
        # 视觉质量评估
        print(f"  ── 视觉质量评估 ──")
        
        issues = []
        
        # 检查是否有大量锯齿/硬边 (alpha值只有0和255，没有过渡)
        if alpha_info["semi_transparent_pct"] < 0.5 and alpha_info["nonzero_alpha_pct"] > 1:
            issues.append("⚠️  半透明像素极少，mask边缘可能缺乏抗锯齿/羽化")
        
        # 检查边缘梯度
        if edge_info["avg_edge_gradient"] > 50:
            issues.append("⚠️  边缘梯度较高，可能有锯齿状边缘")
        
        # 检查mask内部是否有孔洞
        # 简单判断: 检查是否有透明像素被非透明像素包围
        alpha_bin = (rgba[:,:,3] > 0).astype(np.uint8)
        # 检查孤立零值 (holes)
        from scipy.ndimage import binary_closing, label
        try:
            closed = binary_closing(alpha_bin, iterations=3)
            holes = closed.astype(int) - alpha_bin.astype(int)
            hole_count = int(np.sum(holes > 0))
            if hole_count > 100:
                issues.append(f"⚠️  Mask 内部约有 {hole_count} 个孔洞像素(透明区域)")
        except Exception:
            pass
        
        if not issues:
            print(f"  ✅ Mask 质量看起来良好")
        else:
            for issue in issues:
                print(f"  {issue}")
        
        # 检查 RGB 通道是否有异常 (如全黑、全白等)
        rgb = rgba[:, :, :3]
        r_mean, g_mean, b_mean = np.mean(rgb[:,:,0]), np.mean(rgb[:,:,1]), np.mean(rgb[:,:,2])
        print(f"  ── RGB 基本信息 ──")
        print(f"  RGB 均值: R={r_mean:.1f}, G={g_mean:.1f}, B={b_mean:.1f}")
        
    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ 分析文件出错 {filepath}: {e}")
        print(f"{'='*70}")


def main():
    print("=" * 70)
    print("🔍 cut_nail_png  PNG 文件分析报告")
    print(f"   共 {len(FILES)} 个文件")
    print("=" * 70)
    
    for fp in FILES:
        analyze_file(fp)
    
    print("\n" + "=" * 70)
    print("✅ 分析完成")
    print("=" * 70)


if __name__ == "__main__":
    main()