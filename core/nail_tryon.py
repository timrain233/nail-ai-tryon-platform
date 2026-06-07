"""
nail_tryon.py - 美甲在线试戴核心渲染引擎
=============================================
流程：
  1. U2Net ONNX 分割用户手部指甲掩码
  2. MediaPipe 手部关键点识别，区分5指编号
  3. 提取用户指甲四点坐标（后缘左→后缘右→指尖右→指尖左）
  4. 匹配商品ID读取nail_cut3 PNG + nail_points.csv素材四点
  5. 透视变换 + alpha融合
  6. 输出试戴效果图
"""
import cv2
import numpy as np
import os
import csv
import sys
from PIL import Image

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)

# ── 路径常量 ──────────────────────────────────────
RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")
CUT3_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_cut3")
PRODUCT_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_product2")
POINTS_CSV = os.path.join(CUT3_DIR, "nail_points.csv")
SAM_CKPT = os.path.join(_PROJ_ROOT, "models", "sam_vit_b_01ec64.pth")

FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]
FINGER_IDS = [4, 8, 12, 16, 20]

# ── 全局缓存 ──────────────────────────────────────
_u2net_model = None
_mediapipe_hands = None
_sam_predictor = None
_product_cache = {}
_points_cache = None


def _get_onnx_model():
    global _u2net_model
    if _u2net_model is None:
        import onnxruntime as ort
        path = os.path.join(_PROJ_ROOT, "checkpoints", "nail_segment.onnx")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        available = ort.get_available_providers()
        providers = [p for p in providers if p in available]
        _u2net_model = ort.InferenceSession(path, providers=providers)
    return _u2net_model


def _get_mediapipe():
    global _mediapipe_hands
    if _mediapipe_hands is None:
        import mediapipe as mp
        _mediapipe_hands = mp.solutions.hands.Hands(
            static_image_mode=True, max_num_hands=2, min_detection_confidence=0.5
        )
    return _mediapipe_hands


def _get_points_cache():
    global _points_cache
    if _points_cache is None:
        _points_cache = {}
        if os.path.exists(POINTS_CSV):
            with open(POINTS_CSV, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    pid = row["商品ID"]
                    fid = int(row["指头序号"])
                    _points_cache[(pid, fid)] = {
                        "p1": tuple(map(int, row["p1"].split(","))),
                        "p2": tuple(map(int, row["p2"].split(","))),
                        "p3": tuple(map(int, row["p3"].split(","))),
                        "p4": tuple(map(int, row["p4"].split(","))),
                    }
    return _points_cache


def _load_product_nail(product_id, finger_id):
    """加载商品甲片RGBA图片"""
    cache_key = (product_id, finger_id)
    if cache_key in _product_cache:
        return _product_cache[cache_key]

    fname = f"{finger_id}_{FINGER_NAMES[finger_id]}.png"
    path = os.path.join(CUT3_DIR, product_id, fname)
    if not os.path.exists(path):
        _product_cache[cache_key] = None
        return None

    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None or img.shape[-1] != 4:
        _product_cache[cache_key] = None
        return None

    # BGR -> RGB
    rgb = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2RGB)
    alpha = img[..., 3]
    _product_cache[cache_key] = (rgb, alpha)
    return rgb, alpha


def get_available_products():
    """获取所有可用商品ID列表"""
    products = []
    if os.path.exists(CUT3_DIR):
        for d in sorted(os.listdir(CUT3_DIR)):
            dp = os.path.join(CUT3_DIR, d)
            if os.path.isdir(dp) and len(os.listdir(dp)) >= 1:
                products.append(d)
    return products


def get_product_preview_path(product_id):
    """获取商品预览图路径"""
    path = os.path.join(PRODUCT_DIR, product_id, "preview.jpg")
    if os.path.exists(path):
        return path
    return None


def segment_user_hand(rgb):
    """
    U2Net 全图分割用户手部指甲掩码
    返回: full_mask (H×W uint8), 以及每个指甲的独立mask列表
    """
    h, w = rgb.shape[:2]
    model = _get_onnx_model()

    resized = cv2.resize(rgb, (512, 512)).astype(np.float32)
    normalized = resized / 127.5 - 1.0
    inp = normalized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

    input_name = model.get_inputs()[0].name
    output_name = model.get_outputs()[0].name
    out = model.run([output_name], {input_name: inp})[0]

    prob = out[0, 0]
    mask = (prob > 0.5).astype(np.uint8) * 255
    mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

    return mask


def get_fingertips(rgb):
    """MediaPipe 手部关键点识别，返回5个指尖坐标 (0-4)"""
    hands = _get_mediapipe()
    results = hands.process(rgb)
    if not results or not results.multi_hand_landmarks:
        return None

    # 取第一个检测到的手
    h, w = rgb.shape[:2]
    hl = results.multi_hand_landmarks[0]
    pts = []
    for idx in FINGER_IDS:
        lm = hl.landmark[idx]
        pts.append((int(lm.x * w), int(lm.y * h)))
    return pts


def extract_nail_masks(full_mask, fingertips):
    """
    从全图mask中提取每个指甲的独立mask，按手指编号排序
    返回: list of {finger_id, mask, bbox, center}
    """
    h, w = full_mask.shape[:2]
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

    components.sort(key=lambda c: c["area"], reverse=True)

    if not fingertips or len(components) == 0:
        return components

    # 按手指排序
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
            assigned[nearest_f] = comp

    result = []
    for fi in range(5):
        if fi in assigned:
            assigned[fi]["finger_id"] = fi
            result.append(assigned[fi])

    return result


def extract_nail_corners(mask, fingertip=None):
    """
    从指甲掩码提取顺时针四点：后缘左→后缘右→指尖右→指尖左
    利用fingertip确定尖端方向
    """
    ys, xs = np.where(mask > 0)
    if len(xs) < 10:
        return None

    x0, y0 = xs.min(), ys.min()
    x1, y1 = xs.max(), ys.max()

    # 如果无fingertip，假设mask中甲尖在上方（y最小）
    if fingertip is None:
        # 后缘左(左下), 后缘右(右下), 指尖右(右上), 指尖左(左上)
        return [
            (x0, y1),  # 后缘左
            (x1, y1),  # 后缘右
            (x1, y0),  # 指尖右
            (x0, y0),  # 指尖左
        ]

    # 有fingertip：判断尖端在哪个方向
    fx, fy = fingertip
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    dx = fx - cx
    dy = fy - cy

    # 四个角点
    tl = (x0, y0)
    tr = (x1, y0)
    br = (x1, y1)
    bl = (x0, y1)

    # 根据指尖方向确定哪个角是尖端
    corners = [tl, tr, br, bl]
    # 计算每个角到指尖的距离
    dists = [(fx - c[0]) ** 2 + (fy - c[1]) ** 2 for c in corners]

    # 距离指尖最近的两个角是尖端（指尖右、指尖左）
    sorted_idx = np.argsort(dists)
    tip_indices = {sorted_idx[0], sorted_idx[1]}
    root_indices = {sorted_idx[2], sorted_idx[3]}

    # 尖端两个角：区分左右
    tip_corners = [corners[i] for i in tip_indices]
    tip_corners.sort(key=lambda p: p[0])  # 按x排序：左→右
    tip_left, tip_right = tip_corners[0], tip_corners[1]

    # 后缘两个角：区分左右
    root_corners = [corners[i] for i in root_indices]
    root_corners.sort(key=lambda p: p[0])  # 按x排序：左→右
    root_left, root_right = root_corners[0], root_corners[1]

    return [root_left, root_right, tip_right, tip_left]


def apply_nail_tryon(user_rgb, product_id, nail_masks, fingertips):
    """
    核心试戴渲染：对每个指甲进行透视变换+alpha融合
    """
    h, w = user_rgb.shape[:2]
    result = user_rgb.copy().astype(np.float32)

    points_cache = _get_points_cache()
    missing = []

    for nail_info in nail_masks:
        finger_id = nail_info.get("finger_id", -1)
        if finger_id < 0 or finger_id > 4:
            continue

        # 获取用户指甲四点
        ft = fingertips[finger_id] if fingertips and len(fingertips) > finger_id else None
        user_corners = extract_nail_corners(nail_info["mask"], ft)
        if user_corners is None:
            continue

        # 加载商品甲片
        nail_data = _load_product_nail(product_id, finger_id)
        if nail_data is None:
            missing.append(finger_id)
            continue
        nail_rgb, nail_alpha = nail_data

        # 获取商品四点
        src_pts_data = points_cache.get((product_id, finger_id))
        if src_pts_data is None:
            missing.append(finger_id)
            continue

        src_pts = np.array([
            src_pts_data["p1"],  # 后缘左
            src_pts_data["p2"],  # 后缘右
            src_pts_data["p3"],  # 指尖右
            src_pts_data["p4"],  # 指尖左
        ], dtype=np.float32)

        dst_pts = np.array(user_corners, dtype=np.float32)

        # 透视变换
        M = cv2.getPerspectiveTransform(src_pts, dst_pts)

        # 计算输出尺寸（使用用户指甲的bbox）
        mx0 = int(dst_pts[:, 0].min())
        my0 = int(dst_pts[:, 1].min())
        mx1 = int(dst_pts[:, 0].max())
        my1 = int(dst_pts[:, 1].max())

        # 适度扩展ROI
        pad = 10
        mx0 = max(0, mx0 - pad)
        my0 = max(0, my0 - pad)
        mx1 = min(w, mx1 + pad)
        my1 = min(h, my1 + pad)

        out_w = mx1 - mx0
        out_h = my1 - my0
        if out_w <= 0 or out_h <= 0:
            continue

        # 调整透视变换矩阵（平移ROI）
        M_adj = M.copy()
        M_adj[0, 2] -= mx0
        M_adj[1, 2] -= my0

        # 透视变换美甲
        warped_rgb = cv2.warpPerspective(
            nail_rgb, M_adj, (out_w, out_h),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0)
        )
        warped_alpha = cv2.warpPerspective(
            nail_alpha, M_adj, (out_w, out_h),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )

        # Alpha融合
        alpha_f = warped_alpha.astype(np.float32) / 255.0
        alpha_f = np.expand_dims(alpha_f, axis=2)

        roi = result[my0:my1, mx0:mx1]
        blended = warped_rgb.astype(np.float32) * alpha_f + roi * (1 - alpha_f)
        result[my0:my1, mx0:mx1] = blended

    result = np.clip(result, 0, 255).astype(np.uint8)
    return result, missing


def process_tryon(user_image_path, product_id):
    """
    完整试戴流程入口
    参数:
        user_image_path: 用户手部图片路径
        product_id: 商品ID (如 'img_001')
    返回:
        result_rgb: 试戴效果图 (RGB, uint8)
        missing_fingers: 缺失指头编号列表
    """
    bgr = cv2.imread(user_image_path)
    if bgr is None:
        raise ValueError(f"无法读取图片: {user_image_path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    return process_tryon_from_array(rgb, product_id)


def process_tryon_from_array(rgb, product_id):
    """
    从numpy数组处理试戴（用于Flask接口）
    """
    # 1. U2Net 分割
    full_mask = segment_user_hand(rgb)

    # 2. MediaPipe 指尖
    fingertips = get_fingertips(rgb)

    # 3. 提取指甲掩码
    nail_masks = extract_nail_masks(full_mask, fingertips)

    # 4. 试戴渲染
    result_rgb, missing = apply_nail_tryon(rgb, product_id, nail_masks, fingertips)

    if missing:
        print(f"[nail_tryon] 缺失指头: {missing}")

    return result_rgb, missing


# ── 测试入口 ──────────────────────────────────────
if __name__ == "__main__":
    print("nail_tryon.py - 美甲试戴渲染引擎")
    print(f"可用商品: {get_available_products()}")