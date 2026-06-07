"""
nail_segmentor.py - 工业级指甲分割推理引擎
=============================================
支持三种模式:
  1. CVMode (传统视觉): 色彩分析 + 梯度边缘 + 自适应阈值, 开箱即用
  2. ONNXMode (深度学习): 加载 ONNX 模型推理
  3. AutoMode (自动选择): 有 ONNX 模型用 ONNX, 否则用 CV

集成方式:
  segmentor = NailSegmentor(mode="cv")       # 纯 CV
  segmentor = NailSegmentor(mode="onnx")     # ONNX 深度学习
  segmentor = NailSegmentor(mode="auto")     # 自动选择
  result = segmentor.predict(roi_rgb, finger_info)
  # result.mask, result.contour, result.left_seam, result.right_seam, result.root, result.tip

输入: 单指ROI图像 (RGB, H×W×3, uint8)
输出: NailResult 包含所有边界信息
"""

from __future__ import annotations

import os
import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Tuple, List


# ============================================================
# 数据类: 分割结果
# ============================================================

@dataclass
class NailResult:
    """指甲分割的完整输出"""
    mask: np.ndarray              # 二值掩码 (H×W, uint8, 0/255)
    contour: np.ndarray           # 最大轮廓 (N×1×2, int32)
    center: Tuple[int, int]       # 质心 (cx, cy)
    tip: Tuple[int, int]          # 甲尖 (y最小点)
    root: Tuple[int, int]         # 甲根 (y最大点)
    left_seam_start: Tuple[int, int] = (0, 0)   # 左甲缝起点
    left_seam_end: Tuple[int, int] = (0, 0)     # 左甲缝终点
    right_seam_start: Tuple[int, int] = (0, 0)  # 右甲缝起点
    right_seam_end: Tuple[int, int] = (0, 0)    # 右甲缝终点
    left_edge: List[Tuple[int, int]] = field(default_factory=list)   # 左边缘点集
    right_edge: List[Tuple[int, int]] = field(default_factory=list)  # 右边缘点集
    arc_points: np.ndarray = None   # 指甲外缘弧线 (N×2, int32)
    confidence: float = 0.0         # 置信度 0~1
    success: bool = False           # 是否成功

    def to_dict(self) -> dict:
        return {
            'contour': self.contour,
            'center': self.center,
            'tip': self.tip,
            'root': self.root,
            'left_seam_start': self.left_seam_start,
            'left_seam_end': self.left_seam_end,
            'right_seam_start': self.right_seam_start,
            'right_seam_end': self.right_seam_end,
            'left_edge': self.left_edge,
            'right_edge': self.right_edge,
        }


# ============================================================
# 工业级指甲分割引擎
# ============================================================

class NailSegmentor:
    """
    指甲分割引擎
    mode="cv"   : 传统视觉方法, 无需模型 (默认)
    mode="onnx" : 加载 ONNX 模型推理
    mode="auto" : 自动检测 checkpoints/nail_segment.onnx, 有则用 ONNX, 否则用 CV
    """

    def __init__(self, mode: str = "cv", model_path: Optional[str] = None):
        self.mode = mode
        self.model = None
        self._full_mask = None
        self._full_h = 0
        self._full_w = 0

        if mode == "onnx":
            path = model_path or "checkpoints/nail_segment.onnx"
            if not os.path.exists(path):
                raise FileNotFoundError(f"ONNX 模型不存在: {path}")
            self._load_onnx(path)
        elif mode == "auto":
            path = model_path or "checkpoints/nail_segment.onnx"
            if os.path.exists(path):
                print(f"[Auto] 检测到 ONNX 模型: {path}, 使用 ONNX 模式")
                self._load_onnx(path)
                self.mode = "onnx"
            else:
                print(f"[Auto] 未检测到 ONNX 模型, 回退到 CV 模式")
                self.mode = "cv"
        elif mode == "cv":
            pass
        else:
            raise ValueError(f"未知模式: {mode}, 可选: cv, onnx, auto")

    # --------------------------------------------------------
    # ONNX 模型加载
    # --------------------------------------------------------

    def _load_onnx(self, path: str):
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError("请安装 onnxruntime: pip install onnxruntime")

        available = ort.get_available_providers()
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        providers = [p for p in providers if p in available]
        if not providers:
            providers = ["CPUExecutionProvider"]
        self.model = ort.InferenceSession(path, providers=providers)
        self.input_name = self.model.get_inputs()[0].name
        self.input_shape = self.model.get_inputs()[0].shape
        self.output_name = self.model.get_outputs()[0].name

    # --------------------------------------------------------
    # 主推理接口
    # --------------------------------------------------------

    def predict(self, roi_rgb: np.ndarray, finger_info: dict) -> NailResult:
        """
        对单指ROI进行指甲分割

        参数:
            roi_rgb: ROI图像 (H×W×3, RGB, uint8)
            finger_info: {tip:(x,y), dip:(x,y)} 等关键点信息(全局坐标)

        返回:
            NailResult
        """
        if self.mode == "onnx" and self.model is not None:
            return self._predict_onnx(roi_rgb, finger_info)
        else:
            return self._predict_cv(roi_rgb, finger_info)

    def predict_full(self, hand_rgb: np.ndarray):
        if self.mode != "onnx" or self.model is None:
            return

        self._full_h, self._full_w = hand_rgb.shape[:2]

        resized = cv2.resize(hand_rgb, (512, 512)).astype(np.float32)
        normalized = resized / 127.5 - 1.0
        input_tensor = normalized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

        output = self.model.run([self.output_name], {self.input_name: input_tensor})[0]
        prob = output[0, 0]

        mask_full = (prob > 0.5).astype(np.uint8) * 255
        mask_full = cv2.resize(mask_full, (self._full_w, self._full_h),
                               interpolation=cv2.INTER_NEAREST)

        smoothed = np.zeros_like(mask_full)
        contours, _ = cv2.findContours(mask_full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area > 50:
                smooth_c = cv2.approxPolyDP(c, 0.4, True)
                cv2.drawContours(smoothed, [smooth_c], 0, 255, -1)

        erode_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._full_mask = cv2.erode(smoothed, erode_k, iterations=1)

    # --------------------------------------------------------
    # ONNX 推理 (重写 - 完整后处理管线)
    # --------------------------------------------------------

    def _predict_onnx(self, roi_rgb: np.ndarray, finger_info: dict) -> NailResult:
        h, w = roi_rgb.shape[:2]

        if self._full_mask is not None:
            rx, ry, rx2, ry2 = finger_info.get("roi", (0, 0, w, h))
            mask = self._full_mask[ry:ry2, rx:rx2].copy()
            if mask.shape[0] != h or mask.shape[1] != w:
                mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        else:
            resized = cv2.resize(roi_rgb, (512, 512)).astype(np.float32)
            normalized = resized / 127.5 - 1.0
            input_tensor = normalized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
            output = self.model.run([self.output_name], {self.input_name: input_tensor})[0]
            prob_map = cv2.resize(output[0, 0], (w, h), interpolation=cv2.INTER_LINEAR)
            mask = (prob_map > 0.5).astype(np.uint8) * 255
            mask = self._postprocess_mask(mask)

        result = self._extract_boundaries(mask, roi_rgb, finger_info)
        result = self._symmetry_correct(result)
        result.mask = mask
        return result

    def _postprocess_mask(self, mask: np.ndarray) -> np.ndarray:
        mask = self._largest_connected_component(mask)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) > 0:
            largest = max(contours, key=cv2.contourArea)
            smooth_contour = cv2.approxPolyDP(largest, 0.4, True)
            smooth_mask = np.zeros_like(mask)
            cv2.drawContours(smooth_mask, [smooth_contour], 0, 255, -1)
            mask = smooth_mask
        erode_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.erode(mask, erode_k, iterations=1)
        return mask

    # --------------------------------------------------------
    # 传统视觉推理 (工业级CV管道 v2 - 优化版)
    # --------------------------------------------------------

    def _predict_cv(self, roi_rgb: np.ndarray, finger_info: dict) -> NailResult:
        h, w = roi_rgb.shape[:2]
        roi_bgr = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2BGR)

        # [调试] 步骤A - 多模态候选: HSV+YCrCb+LAB+CLAHE+Otsu+梯度 (6通道融合)
        candidates = self._multi_modal_candidates(roi_bgr)
        # [调试] 步骤B - 融合精修: 加权融合→二值化→MORPH_CLOSE(3×3,1)→MORPH_OPEN(3×3,1)
        fused = self._fusion_and_refine(candidates, roi_bgr)
        # [调试] 步骤C - 关键点约束: 基于tip/dip坐标裁剪ROI范围
        constrained = self._apply_keypoint_constraints(fused, finger_info, h, w)
        # [调试] 步骤D - 最大连通域: 保留最大连通区域, 去除小碎片
        mask = self._largest_connected_component(constrained)
        # [调试] 步骤E - 轮廓平滑: 离群剔除→三次多项式拟合→approxPolyDP(0.4)
        mask = self._smooth_contour(mask)
        # [调试] 步骤F - 梯度精修: Sobel梯度边缘修正, dilate(3×3,1)
        mask = self._refine_by_gradient(mask, roi_rgb)
        # [调试] 步骤G - 掩码向内收缩: erode(3×3椭圆核, 1次≈2-3px), 解决甲根/甲沟/甲缝皮肤粘连
        erode_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.erode(mask, erode_k, iterations=1)
        result = self._extract_boundaries(mask, roi_rgb, finger_info)
        result = self._symmetry_correct(result)
        result.mask = mask
        return result

    # ============================================================
    # 子步骤: 多模态候选区域 (v2 - 自适应)
    # ============================================================

    def _multi_modal_candidates(self, roi_bgr: np.ndarray) -> List[np.ndarray]:
        candidates = []

        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        H, S, V = cv2.split(hsv)

        v_low = max(30, int(np.percentile(V, 60)) - 10)
        s_high = min(100, int(np.percentile(S, 40)) + 20)
        nail_hsv = (
            (V > v_low).astype(np.uint8) &
            (S < s_high).astype(np.uint8)
        ).astype(np.uint8) * 255
        candidates.append(nail_hsv)

        ycrcb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2YCrCb)
        Y, Cr, Cb = cv2.split(ycrcb)
        y_low = max(80, int(np.percentile(Y, 55)) - 5)
        cr_mid = int(np.median(Cr))
        nail_ycrcb = (
            (Y > y_low).astype(np.uint8) &
            (np.abs(Cr.astype(np.int16) - cr_mid) < 20).astype(np.uint8)
        ).astype(np.uint8) * 255
        candidates.append(nail_ycrcb)

        lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
        L, A, B = cv2.split(lab)
        l_low = max(60, int(np.percentile(L, 50)) - 5)
        b_high = min(25, int(np.percentile(np.abs(B.astype(np.int16)), 60)))
        nail_lab = (
            (L > l_low).astype(np.uint8) &
            (np.abs(B.astype(np.int16)) < b_high + 10).astype(np.uint8)
        ).astype(np.uint8) * 255
        candidates.append(nail_lab)

        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        adaptive = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 3
        )
        candidates.append(cv2.bitwise_not(adaptive))

        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(otsu)

        grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x**2 + grad_y**2)
        grad_norm = cv2.normalize(grad_mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _, grad_binary = cv2.threshold(grad_norm, 40, 255, cv2.THRESH_BINARY_INV)
        candidates.append(grad_binary)

        return candidates

    # ============================================================
    # 子步骤: 融合 + 精修 (v2 - 梯度保持)
    # ============================================================

    def _fusion_and_refine(self, candidates: List[np.ndarray], roi_bgr: np.ndarray) -> np.ndarray:
        if len(candidates) == 0:
            return np.zeros(candidates[0].shape, dtype=np.uint8)

        fused = cv2.addWeighted(candidates[0], 0.25, candidates[1], 0.20, 0)
        fused = cv2.addWeighted(fused, 1.0, candidates[2], 0.20, 0)
        fused = cv2.addWeighted(fused, 1.0, candidates[3], 0.15, 0)
        fused = cv2.addWeighted(fused, 1.0, candidates[4], 0.10, 0)
        fused = cv2.addWeighted(fused, 1.0, candidates[5], 0.10, 0)

        _, binary = cv2.threshold(fused, 80, 255, cv2.THRESH_BINARY)

        # [调试] 形态学轻量化: MORPH_CLOSE(3×3核, 1次) 仅闭合细小孔洞, 不磨平弧线
        k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k_small, iterations=1)
        # [调试] 形态学轻量化: MORPH_OPEN(3×3核, 1次) 仅去除散点噪点, 不磨平弧线
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_small, iterations=1)
        # [调试] 已删除 medianBlur — 避免磨平指甲弧线

        return binary

    # ============================================================
    # 子步骤: 轮廓平滑 (v2 - 离群剔除+多项式拟合+approxPolyDP)
    # ============================================================

    def _smooth_contour(self, mask: np.ndarray) -> np.ndarray:
        """
        轮廓平滑 (三步流程):
          步骤1 - 离群剔除: 基于质心距离过滤异常轮廓点
          步骤2 - 三次多项式拟合: 分别拟合x/y坐标生成自然弧线
          步骤3 - approxPolyDP(eps=0.4): 保留弧线, 不压成矩形
        """
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) == 0:
            return mask

        largest = max(contours, key=cv2.contourArea)

        # [调试] ===== 步骤1: 离群剔除 ===== (基于原始轮廓点, 非approxPolyDP结果)
        pts = np.array([(int(p[0][0]), int(p[0][1])) for p in largest])
        pts_original_count = len(pts)
        if len(pts) >= 10:
            cx_pt = int(np.mean(pts[:, 0]))
            cy_pt = int(np.mean(pts[:, 1]))
            dists = np.sqrt((pts[:, 0] - cx_pt)**2 + (pts[:, 1] - cy_pt)**2)
            mean_d = np.mean(dists)
            std_d = np.std(dists)
            outlier_threshold = mean_d + 2.0 * std_d
            valid = dists < outlier_threshold
            if np.sum(valid) >= 6:
                pts = pts[valid]
        # [调试] 离群剔除: 阈值={outlier_threshold:.1f}px, {pts_original_count}→{len(pts)}点

        # [调试] ===== 步骤2: 三次多项式拟合 ===== (按轮廓索引分别拟合x/y)
        if len(pts) >= 10:
            idx = np.arange(len(pts), dtype=np.float64)
            try:
                coeffs_x = np.polyfit(idx, pts[:, 0].astype(np.float64), 3)
                coeffs_y = np.polyfit(idx, pts[:, 1].astype(np.float64), 3)
                poly_x = np.poly1d(coeffs_x)
                poly_y = np.poly1d(coeffs_y)
                idx_fine = np.linspace(0, len(pts) - 1, len(pts) * 2)
                pts_fit_x = poly_x(idx_fine).astype(np.int32)
                pts_fit_y = poly_y(idx_fine).astype(np.int32)
                pts_fit_x = np.clip(pts_fit_x, 0, mask.shape[1] - 1)
                pts_fit_y = np.clip(pts_fit_y, 0, mask.shape[0] - 1)
                pts = np.stack([pts_fit_x, pts_fit_y], axis=1)
            except np.linalg.LinAlgError:
                pass
        # [调试] polyfit(deg=3): 生成{len(pts)}个密集拟合点, 保留指甲自然弧线

        # [调试] ===== 步骤3: approxPolyDP(eps=0.4) ===== 最终平滑, 保留弧线不压矩形
        contour_smooth = pts.reshape(-1, 1, 2).astype(np.int32)
        eps = 0.4
        contour_smooth = cv2.approxPolyDP(contour_smooth, eps, True)
        # [调试] approxPolyDP(eps=0.4): {len(contour_smooth)}个轮廓点, 保留自然弧线

        smooth = np.zeros_like(mask)
        cv2.drawContours(smooth, [contour_smooth], 0, 255, -1)

        return smooth

    # ============================================================
    # 子步骤: 梯度精修边缘 (新增)
    # ============================================================

    def _refine_by_gradient(self, mask: np.ndarray, roi_rgb: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2GRAY)
        grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x**2 + grad_y**2)

        grad_norm = cv2.normalize(grad_mag, None, 0, 1, cv2.NORM_MINMAX)
        strong_edge = (grad_norm > 0.25).astype(np.uint8) * 255

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        edge_dilated = cv2.dilate(strong_edge, kernel, iterations=1)

        mask_edge = cv2.Canny(mask, 50, 150)
        # [调试] 梯度精修 dilate: iterations=1 (轻量化, 不磨平弧线)
        mask_edge_dilated = cv2.dilate(mask_edge, kernel, iterations=1)

        overlap = cv2.bitwise_and(edge_dilated, mask_edge_dilated)

        cleaned = mask.copy()
        cleaned[overlap > 0] = 0
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=1)

        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) > 0:
            largest = max(contours, key=cv2.contourArea)
            result = np.zeros_like(cleaned)
            cv2.drawContours(result, [largest], 0, 255, -1)
            return result

        return cleaned

    # ============================================================
    # 子步骤: 关键点约束
    # ============================================================

    def _apply_keypoint_constraints(
        self, mask: np.ndarray, finger_info: dict, h: int, w: int
    ) -> np.ndarray:
        constrained = mask.copy()

        tip_x = finger_info.get("tip_x", w // 2)
        tip_y = finger_info.get("tip_y", h // 2)
        dip_x = finger_info.get("dip_x", w // 2)
        dip_y = finger_info.get("dip_y", h // 2)

        nail_top = min(tip_y, dip_y) - 10
        nail_bottom = max(tip_y, dip_y) + 30
        nail_top = max(0, nail_top)
        nail_bottom = min(h - 1, nail_bottom)

        center_x = (tip_x + dip_x) // 2
        half_width = max(30, (max(tip_x, dip_x) - min(tip_x, dip_x)) * 2 + 20)

        left_bound = max(0, center_x - half_width)
        right_bound = min(w - 1, center_x + half_width)

        vertical_crop = np.zeros_like(mask)
        vertical_crop[nail_top:nail_bottom, :] = 255

        horizontal_crop = np.zeros_like(mask)
        horizontal_crop[:, left_bound:right_bound] = 255

        constrained = cv2.bitwise_and(constrained, vertical_crop)
        constrained = cv2.bitwise_and(constrained, horizontal_crop)

        return constrained

    # ============================================================
    # 子步骤: 最大连通域
    # ============================================================

    def _largest_connected_component(self, mask: np.ndarray) -> np.ndarray:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) == 0:
            return mask

        largest = max(contours, key=cv2.contourArea)
        result = np.zeros_like(mask)
        cv2.drawContours(result, [largest], 0, 255, -1)

        return result

    # ============================================================
    # 子步骤: 边界提取 (v2 - 精准甲缝/甲沟)
    # ============================================================

    def _extract_boundaries(
        self, mask: np.ndarray, roi_rgb: np.ndarray, finger_info: dict
    ) -> NailResult:
        h, w = mask.shape

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) == 0:
            return NailResult(
                mask=np.zeros((h, w), dtype=np.uint8),
                contour=np.zeros((1, 1, 2), dtype=np.int32),
                center=(w // 2, h // 2),
                tip=(w // 2, 0),
                root=(w // 2, h - 1),
                success=False,
                confidence=0.0
            )

        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)

        M = cv2.moments(contour)
        cx = int(M["m10"] / M["m00"]) if M["m00"] > 0 else w // 2
        cy = int(M["m01"] / M["m00"]) if M["m00"] > 0 else h // 2

        smoothed = cv2.approxPolyDP(contour, 0.4, True)
        contour_pts = np.array([(int(p[0][0]), int(p[0][1])) for p in smoothed])

        top_idx = contour_pts[:, 1].argmin()
        bottom_idx = contour_pts[:, 1].argmax()
        tip_point = (int(contour_pts[top_idx][0]), int(contour_pts[top_idx][1]))
        root_point = (int(contour_pts[bottom_idx][0]), int(contour_pts[bottom_idx][1]))

        contour_pts_list = contour_pts.tolist()
        contour_pts_list.sort(key=lambda p: p[1])

        left_edge = []
        right_edge = []
        for px, py in contour_pts_list:
            if px <= cx:
                left_edge.append((px, py))
            else:
                right_edge.append((px, py))

        left_edge.sort(key=lambda x: x[1])
        right_edge.sort(key=lambda x: x[1])

        left_edge = self._smooth_edge_points(left_edge)
        right_edge = self._smooth_edge_points(right_edge)

        left_start = left_edge[0] if left_edge else tip_point
        left_end = left_edge[-1] if left_edge else root_point
        right_start = right_edge[0] if right_edge else tip_point
        right_end = right_edge[-1] if right_edge else root_point

        arc_pts = self._fit_nail_arc(contour, tip_point, cx, cy, h, w)
        confidence = self._evaluate_confidence(mask, area, tip_point, root_point, finger_info, h, w)

        result = NailResult(
            mask=mask,
            contour=smoothed,
            center=(cx, cy),
            tip=tip_point,
            root=root_point,
            left_seam_start=left_start,
            left_seam_end=left_end,
            right_seam_start=right_start,
            right_seam_end=right_end,
            left_edge=left_edge,
            right_edge=right_edge,
            arc_points=arc_pts,
            confidence=confidence,
            success=confidence > 0.3
        )

        gray = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2GRAY)
        result = self._refine_nail_root(result, gray)
        return result

    # ============================================================
    # 子步骤: 边缘点平滑 (新增)
    # ============================================================

    def _smooth_edge_points(self, points: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        if len(points) < 5:
            return points

        xs = np.array([p[0] for p in points], dtype=np.float64)
        ys = np.array([p[1] for p in points], dtype=np.float64)

        window = max(3, len(points) // 10 * 2 + 1)
        if window >= 3:
            xs_smooth = cv2.GaussianBlur(xs, (window, 1), 1.5).flatten()
            ys_smooth = ys.copy()
        else:
            xs_smooth = xs.copy()

        try:
            coeffs = np.polyfit(ys, xs, 3)
            poly_fn = np.poly1d(coeffs)
            xs_fit = poly_fn(ys)
            blended = xs_smooth * 0.4 + xs_fit * 0.6
            return [(int(round(blended[i])), int(round(ys[i]))) for i in range(len(points))]
        except np.linalg.LinAlgError:
            return [(int(round(xs_smooth[i])), int(round(ys[i]))) for i in range(len(points))]

    # ============================================================
    # 子步骤: 对称性校正 (新增)
    # ============================================================

    def _symmetry_correct(self, result: NailResult) -> NailResult:
        cx = result.center[0]
        left = result.left_edge
        right = result.right_edge

        if len(left) < 3 or len(right) < 3:
            return result

        left_by_y = {p[1]: p[0] for p in left}
        right_by_y = {p[1]: p[0] for p in right}

        all_ys = sorted(set(list(left_by_y.keys()) + list(right_by_y.keys())))

        new_left = []
        new_right = []

        for y in all_ys:
            lx = left_by_y.get(y)
            rx = right_by_y.get(y)

            if lx is not None and rx is not None:
                mid = (lx + rx) / 2
                half_w = (rx - lx) / 2
                if half_w > 0:
                    new_lx = int(round(cx - half_w))
                    new_rx = int(round(cx + half_w))
                    new_left.append((new_lx, y))
                    new_right.append((new_rx, y))
                else:
                    new_left.append((lx, y))
                    new_right.append((rx, y))
            elif lx is not None:
                dist = cx - lx
                new_left.append((lx, y))
                new_right.append((int(round(cx + dist)), y))
            elif rx is not None:
                dist = rx - cx
                new_right.append((rx, y))
                new_left.append((int(round(cx - dist)), y))

        new_left.sort(key=lambda x: x[1])
        new_right.sort(key=lambda x: x[1])

        if new_left and new_right:
            old_contour = result.contour
            new_pts = np.array(
                [(x, y) for x, y in new_left] + [(x, y) for x, y in reversed(new_right)],
                dtype=np.int32
            ).reshape(-1, 1, 2)

            result.left_seam_start = new_left[0]
            result.left_seam_end = new_left[-1]
            result.right_seam_start = new_right[0]
            result.right_seam_end = new_right[-1]
            result.left_edge = new_left
            result.right_edge = new_right
            result.contour = new_pts

            all_pts = new_left + new_right
            if all_pts:
                cx_new = sum(p[0] for p in all_pts) // len(all_pts)
                cy_new = sum(p[1] for p in all_pts) // len(all_pts)
                result.center = (cx_new, cy_new)
                result.tip = min(all_pts, key=lambda p: p[1])
                result.root = max(all_pts, key=lambda p: p[1])

        return result

    # ============================================================
    # 子步骤: 甲沟精定位 (新增 - 底部梯度谷值)
    # ============================================================

    def _refine_nail_root(self, result: NailResult, gray: np.ndarray) -> NailResult:
        h, w = gray.shape
        root = result.root
        cx = result.center[0]

        search_top = max(0, root[1] - 15)
        search_bot = min(h - 1, root[1] + 15)

        if search_bot <= search_top:
            return result

        grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        grad_y_abs = np.abs(grad_y)

        band = grad_y_abs[search_top:search_bot,
               max(0, cx - 10):min(w - 1, cx + 10)]
        if band.size == 0:
            return result

        profile = np.mean(band, axis=1)

        valley_peak = np.argmax(profile)
        refined_root_y = search_top + valley_peak

        refined_root_y = max(search_top + 1, min(search_bot - 1, refined_root_y))
        refined_root = (cx, refined_root_y)

        if refined_root[1] > result.tip[1]:
            result.root = refined_root

        left_edge = result.left_edge
        right_edge = result.right_edge

        new_left = [(x, y) for x, y in left_edge if y <= refined_root[1]]
        new_right = [(x, y) for x, y in right_edge if y <= refined_root[1]]

        if new_left:
            result.left_seam_end = new_left[-1]
            result.left_edge = new_left
        if new_right:
            result.right_seam_end = new_right[-1]
            result.right_edge = new_right

        left_pts = [(x, y) for x, y in left_edge if y <= refined_root[1]]
        right_pts = [(x, y) for x, y in right_edge if y <= refined_root[1]]
        result.left_seam_start = left_pts[0] if left_pts else result.left_seam_start
        result.right_seam_start = right_pts[0] if right_pts else result.right_seam_start

        return result

    # ============================================================
    # 外缘弧线拟合 (v2 - 多项式+平滑)
    # ============================================================

    def _fit_nail_arc(
        self,
        contour: np.ndarray,
        tip_point: Tuple[int, int],
        cx: int, cy: int,
        h: int, w: int
    ) -> np.ndarray:
        hull = cv2.convexHull(contour)
        hull_pts = [(int(p[0][0]), int(p[0][1])) for p in hull]

        upper_pts = [p for p in hull_pts if p[1] <= cy + (h // 8)]
        if len(upper_pts) < 8:
            upper_pts = hull_pts[:max(8, len(hull_pts) // 3)]

        if len(upper_pts) >= 8:
            xs = np.array([p[0] for p in upper_pts], dtype=np.float64)
            ys = np.array([p[1] for p in upper_pts], dtype=np.float64)

            try:
                coeffs = np.polyfit(xs, ys, 3)
                poly_fn = np.poly1d(coeffs)

                x_min, x_max = int(xs.min()), int(xs.max())
                margin = max(2, (x_max - x_min) // 10)
                x_range = np.linspace(x_min - margin, x_max + margin, 50)
                y_range = poly_fn(x_range)

                y_range = np.clip(y_range, 0, h - 1)

                arc = np.stack([x_range.astype(np.int32), y_range.astype(np.int32)], axis=1)

                if len(arc) >= 5:
                    arc_float = arc.astype(np.float64)
                    k_size = max(3, len(arc) // 15 * 2 + 1)
                    if k_size >= 3:
                        arc_float[:, 1] = cv2.GaussianBlur(
                            arc_float[:, 1].reshape(-1, 1), (k_size, 1), 1.0
                        ).flatten()
                    return arc_float.astype(np.int32)

                return arc
            except np.linalg.LinAlgError:
                pass

        return np.array(upper_pts, dtype=np.int32)

    # ============================================================
    # 置信度评估
    # ============================================================

    def _evaluate_confidence(
        self, mask: np.ndarray, area: float,
        tip: Tuple[int, int], root: Tuple[int, int],
        finger_info: dict, h: int, w: int
    ) -> float:
        """评估分割质量"""
        score = 0.0

        roi_area = h * w
        area_ratio = area / max(roi_area, 1)
        if 0.05 < area_ratio < 0.7:
            score += 0.3

        mask_h = root[1] - tip[1]
        mask_w = abs(root[0] - tip[0])
        if mask_h > 0 and mask_w > 0:
            aspect = mask_w / mask_h
            if 0.3 < aspect < 2.0:
                score += 0.2

        tip_x = finger_info.get("tip_x", w // 2)
        tip_y = finger_info.get("tip_y", h // 2)
        dist_to_tip = np.sqrt((tip[0] - tip_x)**2 + (tip[1] - tip_y)**2)
        if dist_to_tip < h * 0.3:
            score += 0.3

        if np.abs(root[0] - tip[0]) < w * 0.4:
            score += 0.2

        return min(score, 1.0)


# ============================================================
# ONNX 模型训练脚本 (PyTorch)
# ============================================================

class NailTrainer:
    """
    指甲分割模型训练器
    使用轻量级UNet, 输出可直接导出ONNX
    """

    @staticmethod
    def create_model(input_channels: int = 3, base_filters: int = 32) -> "torch.nn.Module":
        """创建轻量级UNet"""
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            raise ImportError("需要安装 PyTorch: pip install torch")

        class DoubleConv(nn.Module):
            def __init__(self, in_ch, out_ch):
                super().__init__()
                self.conv = nn.Sequential(
                    nn.Conv2d(in_ch, out_ch, 3, padding=1),
                    nn.BatchNorm2d(out_ch),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(out_ch, out_ch, 3, padding=1),
                    nn.BatchNorm2d(out_ch),
                    nn.ReLU(inplace=True),
                )
            def forward(self, x):
                return self.conv(x)

        class UNetLight(nn.Module):
            def __init__(self, in_ch, base=32, out_ch=1):
                super().__init__()
                self.enc1 = DoubleConv(in_ch, base)
                self.enc2 = DoubleConv(base, base * 2)
                self.enc3 = DoubleConv(base * 2, base * 4)
                self.enc4 = DoubleConv(base * 4, base * 8)
                self.pool = nn.MaxPool2d(2)
                self.up3 = nn.ConvTranspose2d(base * 8, base * 4, 2, stride=2)
                self.dec3 = DoubleConv(base * 8, base * 4)
                self.up2 = nn.ConvTranspose2d(base * 4, base * 2, 2, stride=2)
                self.dec2 = DoubleConv(base * 4, base * 2)
                self.up1 = nn.ConvTranspose2d(base * 2, base, 2, stride=2)
                self.dec1 = DoubleConv(base * 2, base)
                self.out = nn.Conv2d(base, out_ch, 1)

            def forward(self, x):
                e1 = self.enc1(x)
                e2 = self.enc2(self.pool(e1))
                e3 = self.enc3(self.pool(e2))
                e4 = self.enc4(self.pool(e3))

                d3 = self.up3(e4)
                d3 = self.dec3(torch.cat([d3, e3], dim=1))
                d2 = self.up2(d3)
                d2 = self.dec2(torch.cat([d2, e2], dim=1))
                d1 = self.up1(d2)
                d1 = self.dec1(torch.cat([d1, e1], dim=1))

                return torch.sigmoid(self.out(d1))

        return UNetLight(input_channels, base_filters)

    @staticmethod
    def export_to_onnx(
        model: "torch.nn.Module",
        output_path: str,
        input_size: Tuple[int, int] = (224, 224)
    ):
        """导出PyTorch模型到ONNX"""
        try:
            import torch
        except ImportError:
            raise ImportError("需要安装 PyTorch")

        model.eval()
        dummy = torch.randn(1, 3, input_size[0], input_size[1])

        torch.onnx.export(
            model, dummy, output_path,
            input_names=["input"],
            output_names=["output"],
            opset_version=11,
            do_constant_folding=True,
        )
        print(f"ONNX模型已保存: {output_path}")

    @staticmethod
    def generate_synthetic_data(
        num_samples: int = 100,
        image_size: Tuple[int, int] = (224, 224),
        output_dir: str = "synthetic_nails"
    ) -> Tuple[List[np.ndarray], List[np.ndarray]]:
        """
        生成合成指甲数据用于训练
        返回: (images_list, masks_list)
        """
        import os

        os.makedirs(f"{output_dir}/images", exist_ok=True)
        os.makedirs(f"{output_dir}/masks", exist_ok=True)

        images = []
        masks = []

        for i in range(num_samples):
            img = np.random.randint(180, 255, (*image_size, 3), dtype=np.uint8)
            mask = np.zeros(image_size, dtype=np.uint8)

            cx = np.random.randint(image_size[1] // 3, image_size[1] * 2 // 3)
            cy = np.random.randint(image_size[0] // 4, image_size[0] * 3 // 4)
            nail_h = np.random.randint(60, 120)
            nail_w = np.random.randint(30, 60)

            for y in range(max(0, cy - nail_h), min(image_size[0], cy + nail_h // 3)):
                row_ratio = (y - (cy - nail_h)) / nail_h
                if row_ratio < 0.2:
                    w = int(nail_w * 0.6 * (row_ratio / 0.2))
                elif row_ratio < 0.7:
                    w = int(nail_w * (0.6 + 0.4 * (row_ratio - 0.2) / 0.5))
                else:
                    w = int(nail_w * (1.0 - 0.3 * (row_ratio - 0.7) / 0.3))
                w = max(3, w)

                lx = max(0, cx - w)
                rx = min(image_size[1] - 1, cx + w)
                if 0 <= y < image_size[0]:
                    img[y, lx:rx] = [np.random.randint(210, 245), np.random.randint(190, 220), np.random.randint(170, 200)]
                    mask[y, lx:rx] = 255

            cv2.imwrite(f"{output_dir}/images/{i:04d}.png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            cv2.imwrite(f"{output_dir}/masks/{i:04d}.png", mask)

            images.append(img)
            masks.append(mask)

        print(f"已生成 {num_samples} 张合成数据到 {output_dir}/")
        return images, masks

    @staticmethod
    def train(
        image_dir: str,
        mask_dir: str,
        num_epochs: int = 50,
        batch_size: int = 8,
        lr: float = 1e-3,
        val_split: float = 0.2,
        image_size: Tuple[int, int] = (224, 224)
    ):
        """训练指甲分割模型"""
        try:
            import torch
            import torch.nn as nn
            import torch.optim as optim
            from torch.utils.data import Dataset, DataLoader
        except ImportError:
            raise ImportError("需要安装 PyTorch: pip install torch")

        class NailDataset(Dataset):
            def __init__(self, img_dir, msk_dir, size, transform=None):
                import os
                from glob import glob
                self.imgs = sorted(glob(f"{img_dir}/*.png") + glob(f"{img_dir}/*.jpg"))
                self.msks = sorted(glob(f"{msk_dir}/*.png") + glob(f"{msk_dir}/*.jpg"))
                self.size = size
                self.transform = transform

            def __len__(self):
                return len(self.imgs)

            def __getitem__(self, idx):
                img_bgr = cv2.imread(self.imgs[idx])
                img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                img_rgb = cv2.resize(img_rgb, self.size).astype(np.float32) / 255.0
                img_rgb = (img_rgb - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
                img_t = torch.FloatTensor(img_rgb).permute(2, 0, 1)

                msk = cv2.imread(self.msks[idx], 0)
                msk = cv2.resize(msk, self.size)
                _, msk = cv2.threshold(msk, 127, 1, cv2.THRESH_BINARY)
                msk_t = torch.FloatTensor(msk).unsqueeze(0)

                return img_t, msk_t

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"训练设备: {device}")

        dataset = NailDataset(image_dir, mask_dir, image_size)
        n_val = max(1, int(len(dataset) * val_split))
        n_train = len(dataset) - n_val
        train_set, val_set = torch.utils.data.random_split(
            dataset, [n_train, n_val]
        )

        train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_set, batch_size=batch_size)

        model = NailTrainer.create_model(base_filters=32).to(device)
        criterion = nn.BCELoss()
        optimizer = optim.AdamW(model.parameters(), lr=lr)

        print(f"开始训练: {num_epochs} epochs, batch={batch_size}")
        for epoch in range(num_epochs):
            model.train()
            train_loss = 0
            for imgs, msks in train_loader:
                imgs, msks = imgs.to(device), msks.to(device)
                optimizer.zero_grad()
                preds = model(imgs)
                loss = criterion(preds, msks)
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            model.eval()
            val_loss = 0
            with torch.no_grad():
                for imgs, msks in val_loader:
                    imgs, msks = imgs.to(device), msks.to(device)
                    preds = model(imgs)
                    val_loss += criterion(preds, msks).item()

            if (epoch + 1) % 10 == 0:
                print(f"Epoch {epoch+1}/{num_epochs} | "
                      f"Train Loss: {train_loss/len(train_loader):.4f} | "
                      f"Val Loss: {val_loss/len(val_loader):.4f}")

        model_path = "nail_model.pth"
        torch.save(model.state_dict(), model_path)
        print(f"模型已保存: {model_path}")

        onnx_path = "nail_model.onnx"
        NailTrainer.export_to_onnx(model, onnx_path, image_size)
        print(f"ONNX模型已导出: {onnx_path}")

        return model_path, onnx_path


# ============================================================
# Gradio 集成示例
# ============================================================

def demo_nail_segmentor():
    """Gradio演示: 上传单指ROI → 显示分割结果"""
    import gradio as gr

    segmentor = NailSegmentor(mode="cv")

    def process(roi_image):
        if roi_image is None:
            return None, None, "请上传图像"

        img = np.array(roi_image)
        h, w = img.shape[:2]
        finger_info = {
            "tip_x": w // 2, "tip_y": int(h * 0.2),
            "dip_x": w // 2, "dip_y": int(h * 0.6),
        }

        result = segmentor.predict(img, finger_info)

        overlay = img.copy()
        if result.success and result.contour is not None:
            cv2.drawContours(overlay, [result.contour], 0, (0, 255, 255), 2)
            cv2.line(overlay, result.left_seam_start, result.left_seam_end, (0, 0, 255), 2)
            cv2.line(overlay, result.right_seam_start, result.right_seam_end, (255, 0, 0), 2)
            cv2.circle(overlay, result.root, 5, (0, 255, 0), -1)
            cv2.circle(overlay, result.tip, 5, (255, 255, 0), -1)

        info = (
            f"置信度: {result.confidence:.2f}\n"
            f"甲尖: {result.tip}  甲根: {result.root}\n"
            f"左甲缝: {result.left_seam_start}→{result.left_seam_end}\n"
            f"右甲缝: {result.right_seam_start}→{result.right_seam_end}"
        )
        return (
            Image.fromarray(result.mask),
            Image.fromarray(overlay),
            info
        )

    with gr.Blocks(title="指甲分割引擎") as demo:
        gr.Markdown("# 工业级指甲分割引擎")
        gr.Markdown("CVMode: 多模态融合 (HSV+YCrCb+自适应阈值), 开箱即用")
        with gr.Row():
            inp = gr.Image(type="pil", label="输入ROI")
            run_btn = gr.Button("分割", variant="primary")
        with gr.Row():
            out_mask = gr.Image(type="pil", label="指甲掩码")
            out_overlay = gr.Image(type="pil", label="边界标注")
        out_info = gr.Textbox(label="分割信息")

        run_btn.click(process, inp, [out_mask, out_overlay, out_info])

    return demo


# ============================================================
# 集成到现有 nail_tryon.py 的适配函数
# ============================================================

def nail_segmentor_predict_wrapper(
    segmentor: NailSegmentor,
    finger_mask: np.ndarray,
    roi_rgb: np.ndarray,
    finger_info: dict
) -> np.ndarray:
    """
    适配器: 将segmentor输出转换为现有pipeline需要的指甲掩码

    参数:
        segmentor: NailSegmentor实例
        finger_mask: 单指掩码 (用于约束范围)
        roi_rgb: ROI图像 (RGB)
        finger_info: 关键点信息

    返回:
        nail_mask: 二值掩码 (uint8, 0/255)
    """
    tip = finger_info["tip"]
    dip = finger_info["dip"]
    roi = finger_info.get("roi", (0, 0, roi_rgb.shape[1], roi_rgb.shape[0]))
    roi_x, roi_y = roi[0], roi[1]

    info = {
        "tip_x": int(tip[0] - roi_x),
        "tip_y": int(tip[1] - roi_y),
        "dip_x": int(dip[0] - roi_x),
        "dip_y": int(dip[1] - roi_y),
        "roi": (roi_x, roi_y, roi[2], roi[3]),
    }

    result = segmentor.predict(roi_rgb, info)

    nail_mask = result.mask.copy()

    nail_mask = cv2.bitwise_and(nail_mask, finger_mask)

    result_mask = np.zeros_like(nail_mask)
    if result.success and result.contour is not None:
        cv2.drawContours(result_mask, [result.contour], 0, 255, -1)

    return cv2.bitwise_or(nail_mask, result_mask) if result.success else nail_mask


# ============================================================
# main: 训练或演示
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "train":
        print("=" * 50)
        print("指甲分割模型训练")
        print("=" * 50)
        print("步骤1: 生成合成数据...")
        NailTrainer.generate_synthetic_data(num_samples=200)
        print()
        print("步骤2: 开始训练...")
        NailTrainer.train(
            image_dir="synthetic_nails/images",
            mask_dir="synthetic_nails/masks",
            num_epochs=50,
            image_size=(224, 224),
        )
    else:
        print("启动Gradio演示...")
        demo = demo_nail_segmentor()
        demo.launch(server_name="0.0.0.0", server_port=7890, share=False)