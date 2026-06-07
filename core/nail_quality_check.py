"""
nail_quality_check.py - 试戴质量自动评分系统
四点贴合度 + 甲片抠图完整度评估
"""
import os, csv, math
import numpy as np

_PROJ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CUT3_DIR = os.path.join(_PROJ, "assets", "nail_cut3")
POINTS_CSV = os.path.join(CUT3_DIR, "nail_points.csv")
FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]


def _load_points():
    """加载 nail_points.csv 四点坐标"""
    data = {}
    if not os.path.exists(POINTS_CSV):
        return data
    try:
        with open(POINTS_CSV, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                pid = row.get("商品ID", "").strip()
                try:
                    fid = int(row.get("指头序号", -1))
                except (ValueError, TypeError):
                    continue
                if not pid or fid < 0 or fid > 4:
                    continue
                pts = []
                for k in ["p1", "p2", "p3", "p4"]:
                    v = row.get(k, "").strip()
                    try:
                        x, y = v.split(",")
                        pts.append((int(x), int(y)))
                    except (ValueError, TypeError):
                        break
                if len(pts) == 4:
                    data[(pid, fid)] = pts
    except Exception:
        pass
    return data


def _product_to_folder(product_id):
    try:
        num = int(product_id)
        return f"img_{num:03d}"
    except (ValueError, TypeError):
        return str(product_id)


def _safe_pt_dist(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)


def _compute_fit_from_corners(user_corners, product_corners):
    user_corners = [(int(x), int(y)) for x, y in user_corners]
    product_corners = [(int(x), int(y)) for x, y in product_corners]

    w_u = max(c[0] for c in user_corners) - min(c[0] for c in user_corners)
    h_u = max(c[1] for c in user_corners) - min(c[1] for c in user_corners)
    w_p = max(c[0] for c in product_corners) - min(c[0] for c in product_corners)
    h_p = max(c[1] for c in product_corners) - min(c[1] for c in product_corners)

    if w_u < 1 or h_u < 1 or w_p < 1 or h_p < 1:
        return 50

    scale = min(w_u / w_p, h_u / h_p) if w_p > 0 and h_p > 0 else 1.0
    scaled_prod = [(int(x * scale), int(y * scale)) for x, y in product_corners]

    u_center = (sum(c[0] for c in user_corners) // 4, sum(c[1] for c in user_corners) // 4)
    p_center = (sum(c[0] for c in scaled_prod) // 4, sum(c[1] for c in scaled_prod) // 4)
    offset = (u_center[0] - p_center[0], u_center[1] - p_center[1])
    aligned_prod = [(x + offset[0], y + offset[1]) for x, y in scaled_prod]

    max_dim = max(w_u, h_u)
    if max_dim < 1:
        return 50

    total_dist = sum(_safe_pt_dist(user_corners[i], aligned_prod[i]) for i in range(4))
    avg_dist = total_dist / 4.0

    raw_score = max(0, 100 - (avg_dist / max_dim * 100))

    clamped = max(0, min(100, raw_score))
    return round(clamped)


def get_fit_score(nail_results, product_id):
    if not nail_results or not product_id:
        return 50
    try:
        points_data = _load_points()
        folder = _product_to_folder(product_id)
        total = 0
        count = 0

        for nr in nail_results:
            fid = nr.get("finger", -1)
            if fid < 0 or fid > 4:
                continue
            if not nr.get("success", False):
                total += 0
                count += 1
                continue

            user_corners = nr.get("corners")
            if user_corners is None or len(user_corners) != 4:
                total += 30
                count += 1
                continue

            prod_corners = points_data.get((folder, fid))
            if prod_corners is None:
                total += 60
                count += 1
                continue

            score = _compute_fit_from_corners(user_corners, prod_corners)
            total += score
            count += 1

        if count == 0:
            return 50
        return round(total / count)
    except Exception:
        return 50


def _load_nail_png(product_id, finger_id):
    folder = _product_to_folder(product_id)
    fname = f"{finger_id}_{FINGER_NAMES[finger_id]}.png"
    path = os.path.join(CUT3_DIR, folder, fname)
    if not os.path.exists(path):
        return None
    from PIL import Image
    try:
        pil = Image.open(path).convert("RGBA")
        arr = np.array(pil)
        return arr
    except Exception:
        return None


def _compute_alpha_quality(arr):
    """检测 alpha 通道完整性"""
    alpha = arr[:, :, 3]
    total = alpha.size
    if total == 0:
        return 0, 0, 0

    coverage = np.count_nonzero(alpha > 10) / total * 100
    mid = np.count_nonzero((alpha > 10) & (alpha < 245)) / total * 100
    return coverage, mid


def _compute_edge_quality(arr):
    """检测边缘平滑度"""
    alpha = arr[:, :, 3]
    binary = (alpha > 10).astype(np.uint8) * 255

    try:
        import cv2
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 100, 0

        c = max(contours, key=cv2.contourArea)
        perimeter = cv2.arcLength(c, True)
        area = cv2.contourArea(c)
        if perimeter < 1:
            return 100, 0

        smoothness = min(100, (4 * math.pi * area) / (perimeter * perimeter) * 100) if perimeter > 0 else 100

        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        defect_ratio = abs(hull_area - area) / max(hull_area, 1) * 100 if hull_area > 0 else 0

        return round(smoothness), round(defect_ratio)
    except Exception:
        return 80, 10


def _check_border_defects(arr):
    """检测边框缺陷（黑边/白边/噪点）"""
    alpha = arr[:, :, 3]
    h, w = arr.shape[:2]
    defects = 0

    # 检查边缘5px内的alpha突变
    margin = 5
    for side in [alpha[:margin, :], alpha[-margin:, :], alpha[:, :margin], alpha[:, -margin:]]:
        has_content = np.any(side > 10)
        has_empty = np.any(side < 10)
        if has_content and has_empty:
            defects += 1

    # 检查内部孤立噪点
    binary = (alpha > 10).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    noise_pixels = np.sum(binary) - np.sum(opened)
    total_content = np.sum(binary)
    if total_content > 0:
        noise_ratio = noise_pixels / total_content * 100
        if noise_ratio > 5:
            defects += 2

    return defects


def _compute_quality_for_nail(arr):
    """对单张甲片计算质量分"""
    try:
        coverage, mid_alpha = _compute_alpha_quality(arr)

        if coverage < 5:
            return 0

        smoothness, defect_ratio = _compute_edge_quality(arr)
        border_defects = _check_border_defects(arr)

        score = 100
        if coverage < 80:
            score -= (80 - coverage) * 0.5
        if mid_alpha > 30:
            score -= (mid_alpha - 30) * 0.3
        if smoothness < 60:
            score -= (60 - smoothness) * 0.4
        score -= defect_ratio * 0.3
        score -= border_defects * 5

        return max(0, min(100, round(score)))
    except Exception:
        return 60


def get_quality_score(product_id):
    if not product_id:
        return 50
    try:
        scores = []
        for fid in range(5):
            arr = _load_nail_png(product_id, fid)
            if arr is None:
                continue
            s = _compute_quality_for_nail(arr)
            scores.append(s)

        if not scores:
            return 50
        avg = sum(scores) / len(scores)
        return round(avg)
    except Exception:
        return 50


def compute_scores(nail_results, product_id):
    fit = get_fit_score(nail_results, product_id)
    quality = get_quality_score(product_id)
    return fit, quality
