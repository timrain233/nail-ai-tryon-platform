"""
nail_renderer.py - 美甲试戴渲染引擎（完整管线）
===============================================
产品抠图 → 全手指甲检测 → 分段扭曲贴合 → 伪3D → 融合

核心流程:
  1. extract_product_nail()    - 从产品图中抠出美甲
  2. detect_all_nails_cv()     - CV检测用户手部所有指甲区域
  3. warp_nail_to_target()     - 将产品甲面分段扭曲到每个指甲
  4. apply_pseudo3d()          - 伪3D光影效果
  5. blend_to_background()     - 自然融合到手部照片
  
依赖: opencv-python-headless, numpy, Pillow
"""
import cv2
import numpy as np
import sys, os, json, csv
from typing import List, Tuple, Optional

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)
from core.nail_segmentor import NailSegmentor, NailResult

# MediaPipe 懒加载
_mp_hands = None

def _get_mp_hands():
    global _mp_hands
    if _mp_hands is None:
        try:
            import mediapipe as mp
            _mp_hands = mp.solutions.hands.Hands(
                static_image_mode=True, max_num_hands=2, min_detection_confidence=0.5
            )
        except ImportError:
            _mp_hands = False
    return _mp_hands if _mp_hands is not False else None

# ── 新架构支持: nail_cut3 + nail_points.csv (U2Net+SAM预处理)
# 商品ID对应关系: raw_images/img_XXX → nail_cut3/img_XXX/[0-4]_finger.png
CUT3_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_cut3")
POINTS_CSV = os.path.join(CUT3_DIR, "nail_points.csv")
OLD_CUTOUT_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_cutouts")
OLD_CUTOUT_META_PATH = os.path.join(OLD_CUTOUT_DIR, "cutout_metadata.json")

# 四点顺序约定 (统一): p1=后缘左, p2=后缘右, p3=指尖右, p4=指尖左 (顺时针)
# → 透视变换源点 dst = 用户指甲四点，顺序一致

# 缓存预提取的抠图
_cutout_cache = {}
_cutout_meta = {}

# ============================================================
# 新架构: nail_cut3 产品加载 + 四点透视变换
# ============================================================

# 缓存: product_id -> {finger_id: (rgb, alpha)}
_product_nail_cache = {}
# 缓存: nail_points.csv 数据
_points_cache = None
FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]
FINGER_TIP_IDS = [4, 8, 12, 16, 20]  # MediaPipe fingertip landmark IDs


def _load_points_data():
    """加载 nail_points.csv 四点坐标"""
    global _points_cache
    if _points_cache is not None:
        return _points_cache
    _points_cache = {}
    if os.path.exists(POINTS_CSV):
        with open(POINTS_CSV, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                pid = row["商品ID"]  # e.g. img_001
                fid = int(row["指头序号"])  # 0-4
                _points_cache[(pid, fid)] = {
                    "p1": tuple(map(int, row["p1"].split(","))),
                    "p2": tuple(map(int, row["p2"].split(","))),
                    "p3": tuple(map(int, row["p3"].split(","))),
                    "p4": tuple(map(int, row["p4"].split(","))),
                }
    return _points_cache


def _product_id_to_folder(product_id):
    """将数字ID转为 img_XXX 格式"""
    try:
        num = int(product_id)
        return f"img_{num:03d}"
    except (ValueError, TypeError):
        return str(product_id)


def load_product_nail(product_id, finger_id):
    """
    从 nail_cut3 加载指定手指的甲片
    product_id: 数字ID (如 "1") 或 img_XXX (如 "img_001")
    finger_id: 0-4
    返回: (rgb, alpha) 或 None
    """
    folder = _product_id_to_folder(product_id)
    cache_key = (folder, finger_id)
    if cache_key in _product_nail_cache:
        return _product_nail_cache[cache_key]

    fname = f"{finger_id}_{FINGER_NAMES[finger_id]}.png"
    path = os.path.join(CUT3_DIR, folder, fname)
    if not os.path.exists(path):
        _product_nail_cache[cache_key] = None
        return None

    bgra = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if bgra is None or bgra.shape[-1] != 4:
        _product_nail_cache[cache_key] = None
        return None

    rgb = cv2.cvtColor(bgra[..., :3], cv2.COLOR_BGR2RGB)
    alpha = bgra[..., 3]
    _product_nail_cache[cache_key] = (rgb, alpha)
    return rgb, alpha


def _get_fingertips(rgb):
    """MediaPipe 手部关键点识别，返回5个指尖坐标 [(x,y),...]"""
    mp_hands = _get_mp_hands()
    if mp_hands is None:
        return None

    results = mp_hands.process(rgb)
    if not results or not results.multi_hand_landmarks:
        return None

    h, w = rgb.shape[:2]
    hl = results.multi_hand_landmarks[0]
    pts = []
    for idx in FINGER_TIP_IDS:
        lm = hl.landmark[idx]
        pts.append((int(lm.x * w), int(lm.y * h)))
    return pts


def _resize_to_640(rgb):
    """
    图片上传自动缩放到长边640px，加速推理
    返回 (resized_rgb, scale_x, scale_y)
    """
    h, w = rgb.shape[:2]
    if max(h, w) <= 640:
        return rgb, 1.0, 1.0
    if h >= w:
        new_h = 640
        new_w = int(w * 640 / h)
    else:
        new_w = 640
        new_h = int(h * 640 / w)
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, w / new_w, h / new_h


def _safe_order_points(pts, fingertip=None, mask=None):
    """带异常捕获的 order_points，失败返回 None"""
    try:
        return order_points(pts, fingertip, mask)
    except Exception:
        return None


def _safe_extract_corners(mask, fingertip=None):
    """带异常捕获的四点提取，失败返回 None"""
    try:
        return _extract_user_nail_corners(mask, fingertip)
    except Exception:
        return None


def order_points(pts, fingertip=None, mask=None):
    """
    四点强制统一排序：将任意顺序的4个点重排为顺时针顺序
    输出: [后缘左, 后缘右, 指尖右, 指尖左] (顺时针)
    
    Args:
        pts: 4个点的list/array [(x,y),...] 任意顺序
        fingertip: MediaPipe指尖坐标 (x,y)，用于判断指甲朝向
        mask: 指甲二值掩码，用于判断指甲方向（fingertip为None时使用）
    
    Returns:
        [(root_left), (root_right), (tip_right), (tip_left)]
    """
    pts = np.array(pts, dtype=np.float32)
    cx, cy = np.mean(pts, axis=0)

    # 按极角顺时针排序（角度递减 → 顺时针）
    angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    sorted_idx = np.argsort(-angles)  # 大到小 = 顺时针
    cw = pts[sorted_idx]  # 4 points clockwise

    # 确定哪两个点是后缘(root)，哪两个是指尖(tip)
    if fingertip is not None:
        fx, fy = fingertip
        dists = np.sqrt((cw[:, 0] - fx)**2 + (cw[:, 1] - fy)**2)
        tip_mask = np.zeros(4, dtype=bool)
        tip_mask[np.argsort(dists)[:2]] = True  # 离指尖最近的2个
    elif mask is not None:
        ys, xs = np.where(mask > 0)
        # mask中y较小→指尖方向，y较大→后缘方向
        tip_mask = cw[:, 1] < (ys.min() + ys.max()) / 2
    else:
        tip_mask = cw[:, 1] < cy  # 默认y较小为指尖

    tip_pts = cw[tip_mask]
    root_pts = cw[~tip_mask]

    # 确保每组2个点
    if len(tip_pts) != 2 or len(root_pts) != 2:
        y_order = np.argsort(cw[:, 1])
        tip_pts = cw[y_order[:2]]
        root_pts = cw[y_order[2:]]

    # 后缘：x小→左, x大→右
    root_left = root_pts[np.argmin(root_pts[:, 0])]
    root_right = root_pts[np.argmax(root_pts[:, 0])]
    # 指尖：x大→右, x小→左
    tip_right = tip_pts[np.argmax(tip_pts[:, 0])]
    tip_left = tip_pts[np.argmin(tip_pts[:, 0])]

    return [
        tuple(root_left.astype(int)),
        tuple(root_right.astype(int)),
        tuple(tip_right.astype(int)),
        tuple(tip_left.astype(int)),
    ]


def _extract_user_nail_corners(mask, fingertip=None):
    """
    从用户指甲掩码提取顺时针四点：后缘左→后缘右→指尖右→指尖左
    
    核心改进：使用 approxPolyDP 从UNet分割轮廓拟合最小四边形，
    而非 minAreaRect 旋转矩形。四边形更贴合指甲真实形状，
    确保透视变换后甲片方向和轮廓与用户指甲一致。
    """
    ys, xs = np.where(mask > 0)
    if len(xs) < 10:
        return None

    # 获取最大轮廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)

    # 用 approxPolyDP 从轮廓拟合四边形
    # 先做凸包保证轮廓是凸的，再用Ramer-Douglas-Peucker逼近到4个顶点
    hull = cv2.convexHull(cnt)
    epsilon = 0.02 * cv2.arcLength(hull, True)
    quad = cv2.approxPolyDP(hull, epsilon, True)

    # 如果一次逼近得不到4个点，逐步放松epsilon
    if len(quad) != 4:
        for scale in [0.03, 0.04, 0.05, 0.06, 0.08, 0.10]:
            quad = cv2.approxPolyDP(hull, scale * cv2.arcLength(hull, True), True)
            if len(quad) == 4:
                break

    # 如果仍然不是4个点，取凸包上极值方向最远的4个点
    if len(quad) != 4:
        hull_pts = hull.squeeze()
        if len(hull_pts.shape) != 2:
            hull_pts = hull.reshape(-1, 2)
        # 按极角均匀采样4个方向的最远点
        cx, cy = np.mean(hull_pts, axis=0)
        angles = np.arctan2(hull_pts[:, 1] - cy, hull_pts[:, 0] - cx)
        bin_edges = np.linspace(-np.pi, np.pi, 5)
        quad_pts = []
        for i in range(4):
            mask_angle = (angles >= bin_edges[i]) & (angles < bin_edges[i + 1])
            if mask_angle.any():
                quad_pts.append(tuple(hull_pts[mask_angle][np.argmax(
                    np.sqrt((hull_pts[mask_angle, 0] - cx)**2 + (hull_pts[mask_angle, 1] - cy)**2)
                )]))
        if len(quad_pts) == 4:
            quad = np.array([quad_pts], dtype=np.int32)
        else:
            # 终极回退：用 minAreaRect
            rect = cv2.minAreaRect(cnt)
            box = cv2.boxPoints(rect)
            return order_points(box, fingertip, mask)

    # 展开为4个点
    box = quad.squeeze()
    if len(box.shape) != 2 or box.shape[0] != 4:
        rect = cv2.minAreaRect(cnt)
        box = cv2.boxPoints(rect)
        return order_points(box, fingertip, mask)

    # order_points 强制排序为 [后缘左, 后缘右, 指尖右, 指尖左]
    return order_points(box.tolist(), fingertip, mask)


def _perspective_warp_blend(dst_img, src_rgb, src_alpha, src_pts, dst_pts):
    """
    透视变换+alpha融合（含dst收缩 + alpha模糊 + 甲根衰减）
    src_pts: 素材四点 [(x,y),...] 顺时针
    dst_pts: 用户指甲四点 [(x,y),...] 顺时针
    """
    try:
        # ★ 四点数量强制检查：src和dst都必须精确等于4个点，否则不渲染
        if len(src_pts) != 4 or len(dst_pts) != 4:
            return dst_img

        h, w = dst_img.shape[:2]
        src_pts_arr = np.array(src_pts, dtype=np.float32)
        dst_pts_arr = np.array(dst_pts, dtype=np.float32)

        # ★ 修改2：dst四点向内收缩2.5%（向几何中心），避免甲片溢出指甲边缘
        shrink_ratio = 0.025
        center = np.mean(dst_pts_arr, axis=0)
        dst_pts_arr = dst_pts_arr * (1 - shrink_ratio) + center * shrink_ratio

        # 计算ROI
        mx0 = int(dst_pts_arr[:, 0].min())
        my0 = int(dst_pts_arr[:, 1].min())
        mx1 = int(dst_pts_arr[:, 0].max())
        my1 = int(dst_pts_arr[:, 1].max())
        pad = 12
        mx0 = max(0, mx0 - pad)
        my0 = max(0, my0 - pad)
        mx1 = min(w, mx1 + pad)
        my1 = min(h, my1 + pad)

        out_w = mx1 - mx0
        out_h = my1 - my0
        if out_w <= 0 or out_h <= 0:
            return dst_img

        try:
            M = cv2.getPerspectiveTransform(src_pts_arr, dst_pts_arr)
        except cv2.error:
            return dst_img

        # 平移ROI
        M_adj = M.copy()
        M_adj[0, 2] -= mx0
        M_adj[1, 2] -= my0

        warped_rgb = cv2.warpPerspective(src_rgb, M_adj, (out_w, out_h),
                                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        warped_alpha = cv2.warpPerspective(src_alpha, M_adj, (out_w, out_h),
                                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

        # ★ 修改3a：alpha 3×3高斯模糊 σ=0.6，消除边缘锯齿歪边
        warped_alpha = cv2.GaussianBlur(warped_alpha, (3, 3), 0.6)

        # ★ 修改3b：甲根后缘1/5区域透明度衰减7%，模拟甲床透肉，修正根部翘起错位
        dst_local = dst_pts_arr - np.array([[mx0, my0]], dtype=np.float32)
        root_mid = np.mean(dst_local[:2], axis=0)   # 后缘边中点
        tip_mid = np.mean(dst_local[2:], axis=0)     # 指尖边中点
        axis_vec = tip_mid - root_mid
        nail_len = np.linalg.norm(axis_vec)

        if nail_len > 1.0:
            axis_vec = axis_vec / nail_len
            yy = np.arange(out_h, dtype=np.float32).reshape(-1, 1)
            xx = np.arange(out_w, dtype=np.float32).reshape(1, -1)
            proj = (xx - root_mid[0]) * axis_vec[0] + (yy - root_mid[1]) * axis_vec[1]

            fade_zone = nail_len * 0.2
            fade_mask = np.ones_like(proj, dtype=np.float32)
            in_zone = (proj >= 0) & (proj < fade_zone)
            fade_mask[in_zone] = 1.0 - 0.07 * (1.0 - proj[in_zone] / fade_zone)

            warped_alpha = (warped_alpha.astype(np.float32) * fade_mask).astype(np.uint8)

        alpha_f = warped_alpha.astype(np.float32) / 255.0
        alpha_f = np.expand_dims(alpha_f, axis=2)

        roi = dst_img[my0:my1, mx0:mx1].astype(np.float32)
        blended = warped_rgb.astype(np.float32) * alpha_f + roi * (1 - alpha_f)
        dst_img[my0:my1, mx0:mx1] = np.clip(blended, 0, 255).astype(np.uint8)
    except Exception:
        pass

    return dst_img


def _detect_nails_with_fingers(hand_rgb, segmentor):
    """
    U2Net全图分割 + MediaPipe手指匹配
    返回: list of {finger_id, mask, bbox, corners}
    """
    h, w = hand_rgb.shape[:2]

    # 1. U2Net full mask
    if segmentor.mode == "onnx" and segmentor.model is not None:
        segmentor.predict_full(hand_rgb)
        full_mask = segmentor._full_mask
    else:
        full_mask = None

    if full_mask is None or cv2.countNonZero(full_mask) < 50:
        # 回退: 使用CV检测
        full_mask = _cv_detect_nails(hand_rgb)

    # 2. 连通域分割
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(full_mask, connectivity=8)
    tp = h * w
    min_a = tp * 0.0002
    max_a = tp * 0.45

    components = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_a or area > max_a:
            continue
        cx, cy = centroids[i]
        cm = (labels == i).astype(np.uint8) * 255
        components.append({
            "mask": cm,
            "bbox": (x, y, bw, bh),
            "center": (int(cx), int(cy)),
            "area": area,
        })

    if not components:
        return []

    # 3. MediaPipe 指尖
    fingertips = _get_fingertips(hand_rgb)

    # 4. 手指匹配
    if fingertips and len(fingertips) == 5:
        components.sort(key=lambda c: c["area"], reverse=True)
        assigned = {}
        for ci, comp in enumerate(components[:5]):
            nearest_f = -1
            nearest_d = 1e9
            for fi, (fx, fy) in enumerate(fingertips):
                d = (comp["center"][0] - fx) ** 2 + (comp["center"][1] - fy) ** 2
                if d < nearest_d and fi not in assigned:
                    nearest_d = d
                    nearest_f = fi
            if nearest_f >= 0:
                # ★ 修改4：编号校验 — finger_id必须在0-4范围内
                if nearest_f < 0 or nearest_f > 4:
                    continue
                # ★ 修改4：无指甲掩码跳过
                if cv2.countNonZero(comp["mask"]) < 30:
                    continue
                assigned[nearest_f] = comp

        result = []
        for fi in range(5):
            if fi in assigned:
                comp = assigned[fi]
                ft = fingertips[fi] if fi < len(fingertips) else None
                corners = _safe_extract_corners(comp["mask"], ft)
                result.append({
                    "finger_id": fi,
                    "mask": comp["mask"],
                    "bbox": comp["bbox"],
                    "center": comp["center"],
                    "corners": corners,
                    "fingertip": ft,
                })
        return result
    else:
        # 无MediaPipe：按面积排序，顺序分配
        components.sort(key=lambda c: c["area"], reverse=True)
        result = []
        for fi, comp in enumerate(components[:5]):
            # ★ 修改4：无指甲掩码跳过
            if cv2.countNonZero(comp["mask"]) < 30:
                continue
            corners = _safe_extract_corners(comp["mask"])
            result.append({
                "finger_id": fi,
                "mask": comp["mask"],
                "bbox": comp["bbox"],
                "center": comp["center"],
                "corners": corners,
                "fingertip": None,
            })
        return result


def _cv_detect_nails(hand_rgb):
    """CV回退：肤色+亮度检测指甲区域"""
    h, w = hand_rgb.shape[:2]
    hand_bgr = cv2.cvtColor(hand_rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(hand_rgb, cv2.COLOR_RGB2GRAY)

    ycrcb = cv2.cvtColor(hand_bgr, cv2.COLOR_BGR2YCrCb)
    Y, Cr, Cb = cv2.split(ycrcb)
    skin = cv2.inRange(ycrcb, np.array([0, 133, 77]), np.array([255, 173, 127]))

    hsv = cv2.cvtColor(hand_bgr, cv2.COLOR_BGR2HSV)
    skin_hsv = cv2.inRange(hsv, np.array([0, 20, 70]), np.array([20, 150, 255]))
    skin = cv2.bitwise_or(skin, skin_hsv)

    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, k5, iterations=2)
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, k3, iterations=1)

    y_blur = cv2.GaussianBlur(Y, (7, 7), 0)
    local_mean = cv2.boxFilter(y_blur, cv2.CV_32F, (31, 31))
    bright = ((y_blur.astype(np.float32) / (local_mean + 1)) > 1.08).astype(np.uint8) * 255
    bright = cv2.bitwise_and(bright, skin)
    nail_cand = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, k5, iterations=2)

    return nail_cand


# ============================================================
# STEP 1: 产品指甲抠图（同前，保持不变）
# ============================================================

def extract_product_nail(product_img: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    从产品图中提取美甲区域，返回 (mask, cutout_rgba)
    """
    h, w = product_img.shape[:2]
    gray = cv2.cvtColor(product_img, cv2.COLOR_RGB2GRAY)

    candidates = []

    # HSV V 通道反向
    hsv = cv2.cvtColor(product_img, cv2.COLOR_RGB2HSV)
    _, _, V = cv2.split(hsv)
    v_th = max(30, int(np.percentile(V, 60)) - 15)
    _, vm = cv2.threshold(V, v_th, 255, cv2.THRESH_BINARY_INV)
    candidates.append(vm)

    # LAB L 通道反向  
    lab = cv2.cvtColor(product_img, cv2.COLOR_RGB2LAB)
    L, _, _ = cv2.split(lab)
    l_th = max(40, int(np.percentile(L, 40)) - 10)
    _, lm = cv2.threshold(L, l_th, 255, cv2.THRESH_BINARY_INV)
    candidates.append(lm)

    # 自适应阈值
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    adapt = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 15, 3)
    candidates.append(adapt)

    # 梯度边缘填充
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gm = np.sqrt(gx ** 2 + gy ** 2)
    gn = cv2.normalize(gm, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, gb = cv2.threshold(gn, 30, 255, cv2.THRESH_BINARY)
    gf = cv2.morphologyEx(gb, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), iterations=2)
    candidates.append(gf)

    # Canny 边缘填充
    edges = cv2.Canny(gray, 30, 100)
    ef = cv2.morphologyEx(edges, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=3)
    candidates.append(ef)

    # 加权融合
    fused = np.zeros_like(gray, dtype=np.float32)
    weights = [0.25, 0.20, 0.15, 0.20, 0.20]
    for cand, wt in zip(candidates, weights):
        if cand is not None:
            fused += cand.astype(np.float32) * wt
    _, binary = cv2.threshold(fused, 80, 255, cv2.THRESH_BINARY)
    binary = binary.astype(np.uint8)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k, iterations=2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k, iterations=1)

    mask = _largest_cc(binary)
    area_ratio = cv2.countNonZero(mask) / max(h * w, 1)

    # 如果产品图本身几乎就是指甲（无背景），直接全区域
    if area_ratio > 0.7:
        mask = np.ones_like(gray, dtype=np.uint8) * 255

    # 收缩一点避免背景杂色
    mask = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)

    # 生成渐变alpha的RGBA
    mask_smooth = cv2.GaussianBlur(mask, (5, 5), 1.5)
    mask_smooth = (mask_smooth > 127).astype(np.uint8) * 255
    mask_smooth = cv2.GaussianBlur(mask_smooth, (3, 3), 1.0)
    alpha = mask_smooth.astype(np.float32) / 255.0
    cutout = np.dstack([product_img, (alpha * 255).astype(np.uint8)])

    return mask, cutout


def _largest_cc(binary: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return binary
    largest = max(contours, key=cv2.contourArea)
    res = np.zeros_like(binary)
    cv2.drawContours(res, [largest], 0, 255, -1)
    return res


# ============================================================
# STEP 2: 全手多指甲检测（ONNX优先，CV回退）
# ============================================================

def detect_all_nails(
    hand_rgb: np.ndarray,
    segmentor: NailSegmentor
) -> List[dict]:
    """
    从手部照片检测所有指甲区域
    优先用 ONNX predict_full()，CV 模式回退到肤色+亮度检测
    
    返回: List[dict] 每个指甲：
      mask, contour, center, tip, root,
      left_edge, right_edge, bbox, confidence
    """
    h, w = hand_rgb.shape[:2]
    results = []

    # === ONNX 模式：predict_full() ===
    if segmentor.mode == "onnx" and segmentor.model is not None:
        segmentor.predict_full(hand_rgb)
        full_mask = segmentor._full_mask

        if full_mask is not None and cv2.countNonZero(full_mask) > 50:
            cnts, _ = cv2.findContours(full_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in cnts:
                area = cv2.contourArea(cnt)
                if area < 200 or area > h * w * 0.4:
                    continue

                bx, by, bw, bh = cv2.boundingRect(cnt)
                pad = 8
                bx = max(0, bx - pad)
                by = max(0, by - pad)
                bw = min(w - bx, bw + pad * 2)
                bh = min(h - by, bh + pad * 2)
                if bw < 15 or bh < 15:
                    continue

                aspect = bh / max(bw, 1)
                if aspect < 0.6 or aspect > 5.0:
                    continue

                roi = hand_rgb[by:by + bh, bx:bx + bw]
                cx_in_roi = int(cnt[:, 0, 0].mean() - bx)
                cy_in_roi = int(cnt[:, 0, 1].mean() - by)

                info = {
                    "tip_x": cx_in_roi,
                    "tip_y": max(0, int(cnt[:, 0, 1].min()) - by),
                    "dip_x": cx_in_roi,
                    "dip_y": min(bh - 1, int(cnt[:, 0, 1].max()) - by),
                    "roi": (bx, by, bx + bw, by + bh)
                }

                try:
                    nr: NailResult = segmentor.predict(roi, info)
                except Exception:
                    continue

                if not nr.success or nr.mask is None:
                    continue
                if cv2.countNonZero(nr.mask) < 30:
                    continue

                results.append({
                    "mask": nr.mask,
                    "contour": nr.contour,
                    "center": (nr.center[0] + bx, nr.center[1] + by),
                    "tip": (nr.tip[0] + bx, nr.tip[1] + by),
                    "root": (nr.root[0] + bx, nr.root[1] + by),
                    "left_edge": [(x + bx, y + by) for x, y in nr.left_edge],
                    "right_edge": [(x + bx, y + by) for x, y in nr.right_edge],
                    "bbox": (bx, by, bw, bh),
                    "confidence": nr.confidence
                })

            results.sort(key=lambda r: r["confidence"], reverse=True)
            if results:
                return results[:10]

    # === CV 回退：肤色+高亮检测 ===
    gray = cv2.cvtColor(hand_rgb, cv2.COLOR_RGB2GRAY)
    hand_bgr = cv2.cvtColor(hand_rgb, cv2.COLOR_RGB2BGR)

    ycrcb = cv2.cvtColor(hand_bgr, cv2.COLOR_BGR2YCrCb)
    Y, Cr, Cb = cv2.split(ycrcb)
    skin = cv2.inRange(ycrcb, np.array([0, 133, 77]), np.array([255, 173, 127]))

    hsv = cv2.cvtColor(hand_bgr, cv2.COLOR_BGR2HSV)
    skin_hsv = cv2.inRange(hsv, np.array([0, 20, 70]), np.array([20, 150, 255]))
    skin = cv2.bitwise_or(skin, skin_hsv)

    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, k5, iterations=2)
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, k3, iterations=1)

    y_blur = cv2.GaussianBlur(Y, (7, 7), 0)
    local_mean = cv2.boxFilter(y_blur, cv2.CV_32F, (31, 31))
    bright = ((y_blur.astype(np.float32) / (local_mean + 1)) > 1.10).astype(np.uint8) * 255
    bright = cv2.bitwise_and(bright, skin)

    nail_cand = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, k5, iterations=2)
    nail_cand = cv2.morphologyEx(nail_cand, cv2.MORPH_OPEN, k3, iterations=1)

    if cv2.countNonZero(nail_cand) < 300:
        bright2 = ((y_blur.astype(np.float32) / (local_mean + 1)) > 1.06).astype(np.uint8) * 255
        bright2 = cv2.bitwise_and(bright2, skin)
        nail_cand = cv2.bitwise_or(nail_cand, bright2)
        nail_cand = cv2.morphologyEx(nail_cand, cv2.MORPH_CLOSE, k5, iterations=3)

    cnts, _ = cv2.findContours(nail_cand, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in cnts:
        area = cv2.contourArea(cnt)
        if area < 200 or area > h * w * 0.4:
            continue

        bx, by, bw, bh = cv2.boundingRect(cnt)
        bx = max(0, bx - 10)
        by = max(0, by - 10)
        bw = min(w - bx, bw + 20)
        bh = min(h - by, bh + 20)
        if bw < 20 or bh < 20:
            continue

        aspect = bh / max(bw, 1)
        if aspect < 0.6 or aspect > 5.0:
            continue

        roi = hand_rgb[by:by + bh, bx:bx + bw]
        cx_in = int(cnt[:, 0, 0].mean() - bx)
        info = {
            "tip_x": cx_in, "tip_y": max(0, int(cnt[:, 0, 1].min()) - by),
            "dip_x": cx_in, "dip_y": min(bh - 1, int(cnt[:, 0, 1].max()) - by),
            "roi": (bx, by, bx + bw, by + bh)
        }

        try:
            nr = segmentor.predict(roi, info)
        except Exception:
            continue
        if not nr.success or nr.mask is None or cv2.countNonZero(nr.mask) < 30:
            continue

        results.append({
            "mask": nr.mask,
            "contour": nr.contour,
            "center": (nr.center[0] + bx, nr.center[1] + by),
            "tip": (nr.tip[0] + bx, nr.tip[1] + by),
            "root": (nr.root[0] + bx, nr.root[1] + by),
            "left_edge": [(x + bx, y + by) for x, y in nr.left_edge],
            "right_edge": [(x + bx, y + by) for x, y in nr.right_edge],
            "bbox": (bx, by, bw, bh),
            "confidence": nr.confidence
        })

    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results[:10]


# ============================================================
# STEP 3: 纹理扭曲贴合
# ============================================================

def warp_nail_art(
    product_rgba: np.ndarray,
    product_mask: np.ndarray,
    target_info: dict
) -> np.ndarray:
    """
    将产品美甲扭曲贴合到目标指甲
    
    参数:
        product_rgba: 产品抠图 RGBA
        product_mask: 产品抠图二值掩码
        target_info: detect_all_nails_cv() 返回的单指甲字典
    
    返回:
        warped_rgba: 扭曲后的贴图 (与目标同尺寸 RGBA)
    """
    # 目标指甲框
    bx, by, bw, bh = target_info["bbox"]
    target_mask = target_info["mask"]

    ph, pw = product_rgba.shape[:2]
    result = np.zeros((bh, bw, 4), dtype=np.uint8)

    # 获取产品指甲的轮廓
    src_contours, _ = cv2.findContours(product_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not src_contours:
        return result
    src_cnt = max(src_contours, key=cv2.contourArea)
    src_pts = np.array([p[0] for p in src_cnt])

    # 获取目标指甲的轮廓（局部坐标）
    tgt_contours, _ = cv2.findContours(target_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not tgt_contours:
        return result
    tgt_cnt = max(tgt_contours, key=cv2.contourArea)
    tgt_pts = np.array([p[0] for p in tgt_cnt])

    # 计算源和目标的质心
    src_cx = int(np.mean(src_pts[:, 0]))
    tgt_cx = int(np.mean(tgt_pts[:, 0]))
    src_min_y, src_max_y = int(src_pts[:, 1].min()), int(src_pts[:, 1].max())
    tgt_min_y, tgt_max_y = int(tgt_pts[:, 1].min()), int(tgt_pts[:, 1].max())

    # 纵向分 N 段做分段透视
    n_seg = 32
    src_left, src_right = _split_edges(src_pts, src_cx)
    tgt_left, tgt_right = _split_edges(tgt_pts, tgt_cx)

    if len(src_left) < 4 or len(src_right) < 4 or len(tgt_left) < 4 or len(tgt_right) < 4:
        # 回退: 直接缩放
        h_ratio = (tgt_max_y - tgt_min_y + 1) / max(src_max_y - src_min_y + 1, 1)
        w_ratio = bw / max(pw, 1)
        scale = min(h_ratio, w_ratio) * 0.8
        new_w = max(1, int(pw * scale))
        new_h = max(1, int(ph * scale))
        resized = cv2.resize(product_rgba, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        ox = max(0, bw // 2 - new_w // 2)
        oy = max(0, bh // 2 - new_h // 2)
        result[oy:oy + new_h, ox:ox + new_w] = resized[:min(new_h, bh - oy), :min(new_w, bw - ox)]
        result[:, :, 3] = np.maximum(result[:, :, 3], target_mask * 255)
        return result

    # 重采样到等分
    src_left = _resample_pts(src_left, n_seg)
    src_right = _resample_pts(src_right, n_seg)
    tgt_left_local = _resample_pts([(x - bx, y - by) for x, y in tgt_left], n_seg)
    tgt_right_local = _resample_pts([(x - bx, y - by) for x, y in tgt_right], n_seg)

    for i in range(n_seg - 1):
        # 源四边形
        src_quad = np.array([
            src_left[i], src_right[i],
            src_right[i + 1], src_left[i + 1]
        ], dtype=np.float32)

        # 目标四边形
        tgt_quad = np.array([
            tgt_left_local[i], tgt_right_local[i],
            tgt_right_local[i + 1], tgt_left_local[i + 1]
        ], dtype=np.float32)

        xmn = max(0, int(np.clip(tgt_quad[:, 0].min(), 0, bw - 1)))
        xmx = min(bw - 1, int(np.clip(tgt_quad[:, 0].max(), 0, bw - 1)))
        ymn = max(0, int(np.clip(tgt_quad[:, 1].min(), 0, bh - 1)))
        ymx = min(bh - 1, int(np.clip(tgt_quad[:, 1].max(), 0, bh - 1)))

        if xmx <= xmn or ymx <= ymn:
            continue

        tw, th = xmx - xmn + 1, ymx - ymn + 1

        # 映射到目标矩形
        tgt_rect = np.array([
            [xmn, ymn], [xmx, ymn], [xmx, ymx], [xmn, ymx]
        ], dtype=np.float32)

        try:
            M = cv2.getPerspectiveTransform(src_quad, tgt_rect)
        except cv2.error:
            continue

        warped = cv2.warpPerspective(product_rgba, M, (tw, th),
                                       flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

        overlay = result[ymn:ymx + 1, xmn:xmx + 1]
        alpha_src = warped[:, :, 3].astype(np.float32) / 255.0
        alpha_dst = overlay[:, :, 3].astype(np.float32) / 255.0
        alpha_out = np.maximum(alpha_src, alpha_dst)

        for c in range(3):
            overlay[:, :, c] = np.where(
                alpha_src > alpha_dst,
                warped[:, :, c],
                overlay[:, :, c]
            )
        overlay[:, :, 3] = (alpha_out * 255).astype(np.uint8)
        result[ymn:ymx + 1, xmn:xmx + 1] = overlay

    result[:, :, 3] = np.maximum(result[:, :, 3], target_mask * 255)
    return result


def _split_edges(pts: np.ndarray, cx: float):
    """按质心左右分割轮廓"""
    sorted_pts = sorted(pts, key=lambda p: p[1])
    left = []
    right = []
    for x, y in sorted_pts:
        if x <= cx:
            left.append((x, y))
        else:
            right.append((x, y))
    return left, right


def _resample_pts(pts: List[Tuple[int, int]], n: int) -> List[Tuple[int, int]]:
    """重采样为 n 个点"""
    if len(pts) < 2:
        return pts[:1] * n if pts else [(0, 0)] * n
    pts = sorted(pts, key=lambda p: p[1])
    indices = np.linspace(0, len(pts) - 1, n, dtype=int)
    return [pts[i] for i in indices]


# ============================================================
# STEP 4: 伪3D渲染效果
# ============================================================

def apply_pseudo3d(
    warped_rgba: np.ndarray,
    nail_mask: np.ndarray,
    tip: Tuple[int, int],
    root: Tuple[int, int],
    left_edge: List[Tuple[int, int]],
    right_edge: List[Tuple[int, int]],
    bbox: Tuple[int, int, int, int]
) -> np.ndarray:
    """
    对已贴合的指甲添加伪3D光影
    
    效果:
      - 穹顶高光: 纵向弧线高光
      - 横向曲面: 左右对称渐变
      - 镜面反光点
      - 边缘阴影（甲沟加深）
      - 根部渐隐（自然过渡）
    """
    bx, by, bw, bh = bbox
    rgb = warped_rgba[:, :, :3].astype(np.float32) / 255.0
    alpha = (nail_mask > 0).astype(np.float32).copy()

    hh, ww = nail_mask.shape
    if cv2.countNonZero(nail_mask) < 10:
        return warped_rgba

    cy = int(np.mean([p[1] for p in left_edge + right_edge])) if left_edge and right_edge else bh // 2

    # 转换为局部坐标
    local_tip = (tip[0] - bx, tip[1] - by)
    local_root = (root[0] - bx, root[1] - by)
    cx_local = local_tip[0]

    y_coords = np.arange(bh).reshape(bh, 1)
    x_coords = np.arange(ww).reshape(1, ww)

    # 1. 纵向穹顶高光
    nail_len = max(1, local_root[1] - local_tip[1])
    y_norm = np.clip((y_coords - local_tip[1]) / nail_len, 0, 1)
    dome = 0.12 * np.sin(y_norm * np.pi)

    # 2. 横向曲面渐变
    dist_cx = np.abs(x_coords - cx_local) / max(ww // 2, 1)
    lateral = 1.0 - 0.08 * np.clip(dist_cx, 0, 1)

    # 3. 镜面高光点
    spec_cx = cx_local
    spec_cy = local_tip[1] + int(nail_len * 0.3)
    spec_r = max(3, int(min(bh, ww) * 0.05))
    sy, sx = np.ogrid[:bh, :ww]
    spec_d = np.sqrt((sx - spec_cx) ** 2 + (sy - spec_cy) ** 2)
    specular = np.exp(-spec_d ** 2 / (2 * max(spec_r ** 2, 1))) * 0.20

    # 4. 边缘阴影
    edge_shade = np.ones((bh, ww), dtype=np.float32)
    if left_edge and right_edge:
        for y in range(bh):
            lxs = [x for x, yy in left_edge if abs(yy - (by + y)) < 3]
            rxs = [x for x, yy in right_edge if abs(yy - (by + y)) < 3]
            if lxs and rxs:
                lx, rx = min(lxs) - bx, max(rxs) - bx
                wd = rx - lx
                if wd > 0:
                    for x in range(ww):
                        if lx < x < rx:
                            d = min(x - lx, rx - x) / (wd // 2)
                            if d < 0.12:
                                edge_shade[y, x] = 1.0 - 0.35 * (1.0 - d / 0.12)

    # 合成
    result = rgb.copy()
    result[:, :, 0] *= (dome * 0.6 + 1.0)
    result[:, :, 1] *= (dome * 0.3 + 1.0)
    result[:, :, 2] *= (dome * 0.2 + 1.0)
    result *= lateral[:, :, np.newaxis]

    for c in range(3):
        result[:, :, c] *= edge_shade

    spec_color = np.array([1.0, 0.95, 0.9])
    for c in range(3):
        result[:, :, c] = np.clip(result[:, :, c] + specular * spec_color[c], 0, 1)

    # 根部渐隐
    root_fade = np.ones((bh, ww), dtype=np.float32)
    fade_start = max(0, local_root[1] - 12)
    for y in range(bh):
        if y > fade_start:
            root_fade[y, :] = max(0, 1.0 - (y - fade_start) / 12.0)

    final_alpha = alpha * root_fade
    final_alpha = cv2.GaussianBlur(final_alpha, (3, 3), 0.8)

    final_rgb = np.clip(result * 255, 0, 255).astype(np.uint8)
    return np.dstack([final_rgb, (final_alpha * 255).astype(np.uint8)])


# ============================================================
# STEP 5: 背景融合
# ============================================================

def blend_to_background(
    rendered_rgba: np.ndarray,
    background_roi: np.ndarray,
    bbox: Tuple[int, int, int, int]
) -> np.ndarray:
    """
    将渲染后的 RGBA 融合到背景 ROI 区域
    """
    bx, by, bw, bh = bbox
    bg = background_roi.astype(np.float32) / 255.0
    alpha = rendered_rgba[:, :, 3].astype(np.float32) / 255.0
    alpha = cv2.GaussianBlur(alpha, (3, 3), 1.0)
    alpha = np.clip(alpha, 0, 1)

    rgb = rendered_rgba[:, :, :3].astype(np.float32) / 255.0
    blended = bg * (1 - alpha[:, :, np.newaxis]) + rgb * alpha[:, :, np.newaxis]
    return np.clip(blended * 255, 0, 255).astype(np.uint8)


# ============================================================
# 主渲染管线
# ============================================================

class NailTryOnRenderer:
    """
    美甲试戴渲染器 (ONNX优先，CV回退) — 单例模式
    
    新架构（优先）: nail_cut3/ + nail_points.csv → U2Net全图分割 → MediaPipe手指匹配 → 透视变换贴合
    旧架构（回退）: nail_cutouts/ 预处理抠图 → 全手检测 → 分段扭曲贴合
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.segmentor = NailSegmentor(mode="auto")
        # 预热 MediaPipe
        _get_mp_hands()
        self._initialized = True

    def _has_cut3_data(self, product_id):
        """检查 nail_cut3 中是否有该商品的预处理甲片"""
        try:
            folder = _product_id_to_folder(product_id)
            path = os.path.join(CUT3_DIR, folder)
            if not os.path.isdir(path):
                return False
            for f in os.listdir(path):
                if f.endswith(".png"):
                    return True
        except Exception:
            pass
        return False

    def render(
        self,
        hand_photo: np.ndarray,
        product_rgb: Optional[np.ndarray] = None,
        product_id: str = ""
    ) -> Tuple[np.ndarray, list]:
        try:
            h, w = hand_photo.shape[:2]
        except Exception:
            return hand_photo if hand_photo is not None else np.zeros((100, 100, 3), dtype=np.uint8), []
        product_id = str(product_id).strip()

        # 自动缩放到640px，加速推理
        hand_photo_resized, sx, sy = _resize_to_640(hand_photo)
        scaled = (hand_photo_resized.shape[1] / hand_photo.shape[1]) if hand_photo.shape[1] > 0 else 1.0

        # ── 优先使用 nail_cut3 四点透视变换管线 ──
        try:
            if product_id and self._has_cut3_data(product_id):
                result_img, render_results = self._render_cut3(hand_photo_resized, product_id)
                # 如果有缩放，将结果等比例放大回原始坐标
                if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
                    oh = int(result_img.shape[0] * sy)
                    ow = int(result_img.shape[1] * sx)
                    if oh > 0 and ow > 0:
                        result_img = cv2.resize(result_img, (ow, oh), interpolation=cv2.INTER_LINEAR)
                return result_img, render_results
        except Exception:
            pass

        # ── 回退: 旧管线 ──
        try:
            return self._render_legacy(hand_photo, product_rgb, product_id)
        except Exception:
            return hand_photo, []

    def _render_cut3(self, hand_photo, product_id):
        """新管线: nail_cut3 + MediaPipe + 四点透视变换（全异常隔离）"""
        try:
            h, w = hand_photo.shape[:2]
            folder = _product_id_to_folder(product_id)
            points_data = _load_points_data()

            # 1. 检测用户指甲 + 手指匹配
            user_nails = _detect_nails_with_fingers(hand_photo, self.segmentor)
            print(f"[Renderer-CUT3] 检测到 {len(user_nails)} 个用户指甲, product={folder}")

            if not user_nails:
                print("[Renderer-CUT3] 未检测到指甲")
                return hand_photo, []

            # 2. 逐指透视变换贴合
            result_img = hand_photo.copy()
            render_results = []
            missing = []

            for nail_info in user_nails:
                finger_id = nail_info.get("finger_id", -1)
                if finger_id < 0 or finger_id > 4:
                    continue

                corners = nail_info.get("corners")
                if corners is None or len(corners) != 4:
                    missing.append(finger_id)
                    continue

                # 加载素材甲片
                src = load_product_nail(product_id, finger_id)
                if src is None:
                    missing.append(finger_id)
                    continue
                src_rgb, src_alpha = src

                # 获取素材四点
                src_pts_data = points_data.get((folder, finger_id))
                if src_pts_data is None:
                    missing.append(finger_id)
                    continue

                src_pts = [
                    src_pts_data["p1"],
                    src_pts_data["p2"],
                    src_pts_data["p3"],
                    src_pts_data["p4"],
                ]
                dst_pts = corners

                # src_pts 和 dst_pts 统一过 order_points 强制排序
                ft = nail_info.get("fingertip")
                src_pts = _safe_order_points(src_pts)
                dst_pts = _safe_order_points(dst_pts, fingertip=ft)

                # 四点数量强制检查，不足4直接跳过
                if src_pts is None or dst_pts is None or len(src_pts) != 4 or len(dst_pts) != 4:
                    missing.append(finger_id)
                    continue

                # 透视变换+融合
                try:
                    result_img = _perspective_warp_blend(
                        result_img, src_rgb, src_alpha, src_pts, dst_pts
                    )
                    render_results.append({
                        "finger": finger_id,
                        "success": True,
                        "corners": dst_pts,
                        "box": [int(c[0]) for c in dst_pts[:2]],
                    })
                    print(f"[Renderer-CUT3] 手指{finger_id}: 贴合成功")
                except Exception as e:
                    print(f"[Renderer-CUT3] 手指{finger_id} 失败: {e}")
                    render_results.append({
                        "finger": finger_id,
                        "success": False,
                        "error": str(e),
                    })

            if missing:
                print(f"[Renderer-CUT3] 缺失指头素材: {missing}")

            return result_img, render_results
        except Exception as e:
            print(f"[Renderer-CUT3] 严重错误: {e}")
            return hand_photo, []

    def _render_legacy(self, hand_photo, product_rgb, product_id):
        """旧管线回退: nail_cutouts 或实时抠图 + 分段扭曲"""
        h, w = hand_photo.shape[:2]

        # 1. 优先用旧预处理抠图
        prod_mask, prod_cutout = None, None
        if product_id:
            try:
                precut = get_precut(int(product_id))
                if precut:
                    prod_mask, prod_cutout = precut
                    print(f"[Renderer-LEGACY] 使用预处理抠图: product_id={product_id}")
            except Exception:
                pass

        if prod_cutout is None and product_rgb is not None:
            prod_mask, prod_cutout = extract_product_nail(product_rgb)
            print(f"[Renderer-LEGACY] 实时抠图: shape={prod_cutout.shape}")

        if prod_cutout is None:
            print("[Renderer-LEGACY] 无产品抠图!")
            return hand_photo, []

        # 2. 全手多指甲检测
        nail_infos = detect_all_nails(hand_photo, self.segmentor)
        print(f"[Renderer-LEGACY] 检测到 {len(nail_infos)} 个指甲")

        if not nail_infos:
            center_x, center_y = w // 2, h // 2
            nail_w, nail_h = w // 4, h // 3
            fake_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.ellipse(fake_mask, (center_x, center_y), (nail_w, nail_h), 0, 0, 360, 255, -1)
            bx, by = center_x - nail_w, center_y - nail_h
            bw, bh = nail_w * 2, nail_h * 2
            left_edge = [(center_x - nail_w, y) for y in range(center_y - nail_h, center_y + nail_h, 5)]
            right_edge = [(center_x + nail_w, y) for y in range(center_y - nail_h, center_y + nail_h, 5)]
            nail_infos.append({
                "mask": fake_mask[by:by + bh, bx:bx + bw],
                "contour": None, "center": (center_x, center_y),
                "tip": (center_x, center_y - nail_h), "root": (center_x, center_y + nail_h),
                "left_edge": left_edge, "right_edge": right_edge,
                "bbox": (bx, by, bw, bh), "confidence": 0.3
            })

        result_img = hand_photo.copy()
        render_results = []
        for idx, ninfo in enumerate(nail_infos):
            try:
                bx, by, bw, bh = ninfo["bbox"]
                if bw < 5 or bh < 5:
                    continue
                warped = warp_nail_art(prod_cutout, prod_mask, ninfo)
                rendered = apply_pseudo3d(warped, ninfo["mask"], ninfo["tip"], ninfo["root"],
                                          ninfo["left_edge"], ninfo["right_edge"], ninfo["bbox"])
                bg_roi = result_img[by:by + bh, bx:bx + bw]
                blended = blend_to_background(rendered, bg_roi, ninfo["bbox"])
                result_img[by:by + bh, bx:bx + bw] = blended
                render_results.append({"finger": idx, "bbox": [int(x) for x in (bx, by, bw, bh)],
                                       "center": [int(x) for x in ninfo["center"]],
                                       "confidence": float(ninfo["confidence"]), "success": True})
            except Exception as e:
                render_results.append({"finger": idx, "success": False, "error": str(e)})

        return result_img, render_results


# ── 兼容旧接口 ──
def get_precut(product_id: int):
    """兼容旧版: 从 nail_cutouts/ 加载, 若无则返回None"""
    path = os.path.join(OLD_CUTOUT_DIR, "cutout_metadata.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return None
    pid = str(product_id)
    info = meta.get(pid)
    if not info:
        return None
    cutout_path = os.path.join(OLD_CUTOUT_DIR, os.path.basename(info.get("cutout", "")))
    if not os.path.exists(cutout_path):
        cutout_path = info.get("cutout", "")
        if not os.path.isabs(cutout_path):
            cutout_path = os.path.join(_PROJ_ROOT, cutout_path)
    if not os.path.exists(cutout_path):
        return None
    from PIL import Image
    pil = Image.open(cutout_path).convert("RGBA")
    rgba = np.array(pil)
    mask_path = os.path.join(OLD_CUTOUT_DIR, os.path.basename(info.get("mask", "")))
    mask = None
    if os.path.exists(mask_path):
        mask = np.array(Image.open(mask_path).convert("L"))
    return (mask, rgba)


# ============================================================
# 简化入口
# ============================================================

def single_nail_tryon(hand_photo: np.ndarray, product_rgb: np.ndarray, pid: str = "") -> np.ndarray:
    renderer = NailTryOnRenderer()
    result, _ = renderer.render(hand_photo, product_rgb, pid)
    return result


if __name__ == "__main__":
    print("=" * 50)
    print("NAIL AI - 美甲试戴渲染引擎 v3 (nail_cut3 + 四点透视)")
    print("=" * 50)
    print("新管线:")
    print("  1. U2Net ONNX 全图分割 → 连通域提取")
    print("  2. MediaPipe 手指识别 → 0-4指编号匹配")
    print("  3. nail_cut3/ + nail_points.csv 素材四点加载")
    print("  4. cv2.getPerspectiveTransform 透视变换贴合")
    print("  5. alpha通道融合输出")
    print("旧管线回退: nail_cutouts/ 分段扭曲贴合")
