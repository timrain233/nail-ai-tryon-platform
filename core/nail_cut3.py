"""
U2Net + SAM 联合美甲抠图 v3
- U2Net: 主体甲面分割
- SAM: 钻石/闪粉/边缘细节补全
- MediaPipe: 手部5指排序
- minAreaRect 旋转矫正 + 四点坐标 + 留白
- 粘连检测 + nail_config.csv
"""
import cv2
import numpy as np
import os
import glob
import sys
import csv
from PIL import Image

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)
from core.nail_segmentor import NailSegmentor
import mediapipe as mp

# ── 路径 ──────────────────────────────────────────
RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")
OUT_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_cut3")
SAM_CKPT = os.path.join(_PROJ_ROOT, "models", "sam_vit_b_01ec64.pth")
CSV_PATH = os.path.join(OUT_DIR, "nail_config.csv")

# ── 清空输出目录 ──────────────────────────────────
os.makedirs(OUT_DIR, exist_ok=True)
for sub in os.listdir(OUT_DIR):
    sp = os.path.join(OUT_DIR, sub)
    if os.path.isdir(sp):
        for f in os.listdir(sp):
            try: os.remove(os.path.join(sp, f))
            except: pass
        try: os.rmdir(sp)
        except: pass

# ── 加载模型 ──────────────────────────────────────
print("加载 U2Net...")
segmentor = NailSegmentor(mode="auto")
print(f"  U2Net: {segmentor.mode}")

print("加载 SAM vit_b...")
import torch
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"  device: {device}")

from segment_anything import sam_model_registry, SamPredictor
sam = sam_model_registry["vit_b"](checkpoint=SAM_CKPT)
sam.to(device=device)
sam.eval()
predictor = SamPredictor(sam)

print("加载 MediaPipe Hands...")
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(static_image_mode=True, max_num_hands=2, min_detection_confidence=0.5)

# ── 常量 ──────────────────────────────────────────
FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]
FINGER_IDS = [4, 8, 12, 16, 20]
PAD_MARGIN = 8   # 裁剪留白像素
K1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (1, 1))

# ── CSV 记录 ──────────────────────────────────────
csv_rows = []


def get_fingertips(rgb):
    h, w = rgb.shape[:2]
    results = hands.process(rgb)
    if not results or not results.multi_hand_landmarks:
        return []
    pts = []
    for hl in results.multi_hand_landmarks:
        for idx in FINGER_IDS:
            lm = hl.landmark[idx]
            pts.append((int(lm.x * w), int(lm.y * h)))
    return pts


def get_u2net_mask(rgb):
    segmentor.predict_full(rgb)
    m = segmentor._full_mask
    if m is None or cv2.countNonZero(m) == 0:
        return None
    return m.copy()


def detect_merged_nails(rgb, full_mask):
    """检测多甲粘连：检查是否有连通域面积超过阈值"""
    h, w = full_mask.shape[:2]
    tp = h * w
    max_a = tp * 0.40  # 单个指甲最大面积
    n, labels, stats, _ = cv2.connectedComponentsWithStats(full_mask, connectivity=8)
    merged = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area > max_a:
            merged.append(area)
    return len(merged) > 0


def split_nails_from_mask(rgb, full_mask):
    """从U2Net全图mask中提取指甲区域，返回独立指甲+粘连标记"""
    h, w = full_mask.shape[:2]
    tp = h * w
    min_a = tp * 0.0003
    max_a = tp * 0.40

    # 仅1x1闭运算填充内部孔洞，禁止腐蚀
    clean = cv2.morphologyEx(full_mask, cv2.MORPH_CLOSE, K1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(clean, connectivity=8)
    nails = []
    merged_count = 0

    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_a:
            continue
        if area > max_a:
            merged_count += 1
            # 粘连块过大，但保留文件（不丢弃）
            cm = (labels == i).astype(np.uint8) * 255
            local_mask = cm[y:y+bh, x:x+bw]
            nails.append({
                "mask": local_mask,
                "bbox": (x, y, bw, bh),
                "area": area,
                "label": i,
                "is_merged": True,
            })
        else:
            cm = (labels == i).astype(np.uint8) * 255
            local_mask = cm[y:y+bh, x:x+bw]
            nails.append({
                "mask": local_mask,
                "bbox": (x, y, bw, bh),
                "area": area,
                "label": i,
                "is_merged": False,
            })

    nails.sort(key=lambda n: n["area"], reverse=True)
    return nails[:5], merged_count > 0


def refine_with_sam(rgb, box, u2net_mask_local):
    """SAM精修：box prompt + U2Net mask作为参考，OR融合"""
    x, y, bw, bh = box
    pad = 30
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(rgb.shape[1], x + bw + pad)
    y2 = min(rgb.shape[0], y + bh + pad)

    crop = rgb[y1:y2, x1:x2].copy()
    hc, wc = crop.shape[:2]
    if hc < 10 or wc < 10:
        return u2net_mask_local

    box_crop = np.array([x - x1, y - y1, x + bw - x1, y + bh - y1])

    predictor.set_image(crop)
    masks, scores, _ = predictor.predict(
        box=box_crop[None, :],
        multimask_output=True,
    )

    best_sam = None
    best_iou = 0
    u2net_crop = np.zeros((hc, wc), dtype=np.uint8)
    u2net_crop[y - y1:y - y1 + bh, x - x1:x - x1 + bw] = u2net_mask_local

    for j in range(masks.shape[0]):
        sam_m = masks[j].astype(np.uint8) * 255
        inter = cv2.bitwise_and(sam_m, u2net_crop)
        union = cv2.bitwise_or(sam_m, u2net_crop)
        iou = cv2.countNonZero(inter) / (cv2.countNonZero(union) + 1)
        if iou > best_iou:
            best_iou = iou
            best_sam = sam_m

    if best_sam is None:
        return u2net_mask_local

    full_mask = np.zeros((rgb.shape[0], rgb.shape[1]), dtype=np.uint8)
    full_mask[y:y+bh, x:x+bw] = u2net_mask_local
    full_mask[y1:y2, x1:x2] = cv2.bitwise_or(
        full_mask[y1:y2, x1:x2],
        best_sam
    )
    return full_mask


def get_mask_corners(mask):
    """从mask获取四点坐标：左上、右上、右下、左下"""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return [(0, 0), (0, 0), (0, 0), (0, 0)]
    nx0, ny0 = xs.min(), ys.min()
    nx1, ny1 = xs.max(), ys.max()
    return [
        (int(nx0), int(ny0)),
        (int(nx1), int(ny0)),
        (int(nx1), int(ny1)),
        (int(nx0), int(ny1)),
    ]


def rotate_and_align(rgb, mask, tip_dir=None):
    """旋转矫正：甲尖竖直朝上。tip_dir=(dx,dy)是原始图像中从甲中心指向甲尖的向量"""
    h, w = mask.shape[:2]
    if h < 10 or w < 10:
        return rgb, mask, get_mask_corners(mask)

    # 方案1：有tip_dir时，直接用指尖方向确定旋转角度
    if tip_dir is not None and abs(tip_dir[0]) + abs(tip_dir[1]) > 5:
        dx, dy = tip_dir
        # 甲尖应该朝上(y轴负方向)，计算需要旋转的角度
        target_angle = -np.degrees(np.arctan2(dx, -dy))  # 旋转dx使其指向(0,-1)
        angle = target_angle
    else:
        # 方案2：无tip_dir时用PCA
        ys, xs = np.where(mask > 0)
        if len(xs) < 10:
            return rgb, mask, get_mask_corners(mask)
        mean_x, mean_y = np.mean(xs), np.mean(ys)
        cov = np.cov(xs - mean_x, ys - mean_y)
        eigenvalues, eigenvectors = np.linalg.eig(cov)
        idx = np.argmax(eigenvalues)
        main_axis = eigenvectors[:, idx]
        angle = np.degrees(np.arctan2(main_axis[0], main_axis[1]))

    # 旋转
    cy, cx = h / 2, w / 2
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    nw = int(h * sin + w * cos)
    nh = int(h * cos + w * sin)
    M[0, 2] += nw / 2 - cx
    M[1, 2] += nh / 2 - cy

    rot_rgb = cv2.warpAffine(rgb, M, (nw, nh), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    rot_mask = cv2.warpAffine(mask, M, (nw, nh), flags=cv2.INTER_NEAREST,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    # 裁剪到mask实际范围
    ys, xs = np.where(rot_mask > 0)
    if len(xs) == 0:
        return rot_rgb, rot_mask, [(0, 0)] * 4

    x0, y0 = xs.min(), ys.min()
    x1, y1 = xs.max(), ys.max()

    # 留白
    x0 = max(0, x0 - PAD_MARGIN)
    y0 = max(0, y0 - PAD_MARGIN)
    x1 = min(rot_rgb.shape[1], x1 + PAD_MARGIN)
    y1 = min(rot_rgb.shape[0], y1 + PAD_MARGIN)

    crop_rgb = rot_rgb[y0:y1, x0:x1]
    crop_mask = rot_mask[y0:y1, x0:x1]

    # 强制验证：旋转后甲根应比甲尖宽，若上方更宽说明方向反了
    h_c = crop_mask.shape[0]
    if h_c >= 20:
        top_band = crop_mask[:max(1, int(h_c * 0.2)), :]
        bot_band = crop_mask[int(h_c * 0.8):, :]
        tw = np.count_nonzero(top_band.any(axis=0))
        bw = np.count_nonzero(bot_band.any(axis=0))
        if bw > 0 and tw > bw:
            crop_rgb = cv2.rotate(crop_rgb, cv2.ROTATE_180)
            crop_mask = cv2.rotate(crop_mask, cv2.ROTATE_180)

    # 四点坐标：mask外接矩形的四个角（左上、右上、右下、左下）
    four_corners = get_mask_corners(crop_mask)

    return crop_rgb, crop_mask, four_corners


def find_finger_order(nail_bboxes, fingertips):
    """将指甲按 MediaPipe 指尖排序: 0=拇指,1=食指,2=中指,3=无名指,4=小指"""
    if len(fingertips) < 5 or len(nail_bboxes) < 5:
        return list(range(len(nail_bboxes)))

    # 为每个指甲找最近指尖
    finger_to_nail = {}
    for i, (x, y, bw, bh) in enumerate(nail_bboxes):
        cx = x + bw // 2
        cy = y + bh // 2
        nearest_f = -1
        nearest_d = 1e9
        for fi, (fx, fy) in enumerate(fingertips):
            d = (cx - fx) ** 2 + (cy - fy) ** 2
            if d < nearest_d:
                nearest_d = d
                nearest_f = fi
        if nearest_f not in finger_to_nail:
            finger_to_nail[nearest_f] = i

    # 按手指顺序排列
    ordered = []
    for finger_idx in range(5):
        if finger_idx in finger_to_nail:
            ordered.append(finger_to_nail[finger_idx])
        else:
            for ni in range(len(nail_bboxes)):
                if ni not in ordered:
                    ordered.append(ni)
                    break
    return ordered


def save_rgba(path, rgb, mask):
    rgba = np.dstack([rgb, mask])
    Image.fromarray(rgba, "RGBA").save(path, "PNG")


# ── 主流程 ────────────────────────────────────────
paths = sorted(glob.glob(os.path.join(RAW_DIR, "*")))
print(f"\n共 {len(paths)} 张商品图\n")

for p in paths:
    fname = os.path.basename(p)
    name = os.path.splitext(fname)[0]
    print(f"[{name}]")

    bgr = cv2.imread(p)
    if bgr is None:
        print(f"  读取失败")
        continue

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]

    # 1. U2Net 主体分割
    u2net_mask = get_u2net_mask(rgb)
    if u2net_mask is None:
        print(f"  U2Net无掩码")
        continue

    # 2. 提取指甲 + 粘连检测
    nails, has_merged = split_nails_from_mask(rgb, u2net_mask)

    if not nails:
        print(f"  无有效指甲")
        continue

    # 3. MediaPipe 指尖
    fingertips = get_fingertips(rgb)

    # 4. SAM 精修每个指甲
    enhanced_nails = []
    for nail in nails:
        x, y, bw, bh = nail["bbox"]
        u2net_local = nail["mask"]
        is_merged = nail.get("is_merged", False)

        refined_mask = refine_with_sam(rgb, (x, y, bw, bh), u2net_local)

        ys, xs = np.where(refined_mask > 0)
        if len(xs) == 0:
            continue

        nx, ny = xs.min(), ys.min()
        nw = xs.max() - nx + 1
        nh = ys.max() - ny + 1

        final_mask = refined_mask[ny:ny+nh, nx:nx+nw]
        final_rgb = rgb[ny:ny+nh, nx:nx+nw].copy()

        enhanced_nails.append({
            "rgb": final_rgb,
            "mask": final_mask,
            "bbox": (x, y, bw, bh),
            "crop_bbox": (nx, ny, nw, nh),
            "is_merged": is_merged,
        })

    # 5. MediaPipe 补甲
    if len(enhanced_nails) < 5 and segmentor.model is not None and fingertips:
        existing_masks = np.zeros((h, w), dtype=np.uint8)
        for n in enhanced_nails:
            nx, ny, nw, nh = n["crop_bbox"]
            existing_masks[ny:ny+nh, nx:nx+nw] = cv2.bitwise_or(
                existing_masks[ny:ny+nh, nx:nx+nw],
                cv2.resize(n["mask"], (nw, nh), interpolation=cv2.INTER_NEAREST)
            )

        for fx, fy in fingertips:
            if len(enhanced_nails) >= 5:
                break
            bx = max(0, fx - 35)
            by = max(0, fy - 35)
            bw = min(70, w - bx)
            bh = min(90, h - by)
            if bw < 15 or bh < 15:
                continue
            if existing_masks[fy, fx] > 0:
                continue

            crop = rgb[by:by+bh, bx:bx+bw]
            hc, wc = crop.shape[:2]
            inp = cv2.resize(crop, (512, 512)).astype(np.float32) / 127.5 - 1.0
            inp = inp.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
            out = segmentor.model.run(
                [segmentor.output_name], {segmentor.input_name: inp})[0]
            prob = cv2.resize(out[0, 0], (wc, hc), interpolation=cv2.INTER_LINEAR)
            for th in [0.2, 0.1, 0.05, 0.03]:
                _, lm = cv2.threshold(prob, th, 255, cv2.THRESH_BINARY)
                lm = lm.astype(np.uint8)
                if cv2.countNonZero(lm) >= 10:
                    break
            if cv2.countNonZero(lm) < 10:
                continue

            enhanced_nails.append({
                "rgb": crop.copy(),
                "mask": lm,
                "bbox": (bx, by, bw, bh),
                "crop_bbox": (bx, by, bw, bh),
                "is_merged": False,
            })
            existing_masks[by:by+bh, bx:bx+bw] = cv2.bitwise_or(
                existing_masks[by:by+bh, bx:bx+bw],
                cv2.resize(lm, (bw, bh), interpolation=cv2.INTER_NEAREST)
            )

    # 6. 手指排序
    nail_bboxes = [(n["crop_bbox"][0], n["crop_bbox"][1],
                    n["crop_bbox"][2], n["crop_bbox"][3]) for n in enhanced_nails]
    order = find_finger_order(nail_bboxes, fingertips)

    # 7. 为每个nail匹配指尖并计算tip方向
    nail_tip_dirs = {}
    if fingertips:
        for i, n in enumerate(enhanced_nails):
            nx, ny, nw, nh = n["crop_bbox"]
            nc_x = nx + nw // 2
            nc_y = ny + nh // 2
            best_f = None
            best_d = 1e9
            for fi, (fx, fy) in enumerate(fingertips):
                d = (nc_x - fx) ** 2 + (nc_y - fy) ** 2
                if d < best_d:
                    best_d = d
                    best_f = fi
            if best_f is not None:
                fx, fy = fingertips[best_f]
                dx = fx - nc_x  # 从甲中心指向指尖
                dy = fy - nc_y
                nail_tip_dirs[i] = (dx, dy)

    # 8. 旋转 + 保存 + CSV
    sub = os.path.join(OUT_DIR, name)
    os.makedirs(sub, exist_ok=True)
    saved = 0
    for finger_idx in range(5):
        if finger_idx >= len(order):
            continue
        ni = order[finger_idx]
        if ni >= len(enhanced_nails):
            continue

        nail = enhanced_nails[ni]
        rgb_piece = nail["rgb"]
        mask_piece = nail["mask"]
        is_merged = nail.get("is_merged", False)

        if rgb_piece.shape[:2] != mask_piece.shape[:2]:
            mask_piece = cv2.resize(mask_piece, (rgb_piece.shape[1], rgb_piece.shape[0]),
                                     interpolation=cv2.INTER_NEAREST)

        # 旋转矫正
        tip_dir = nail_tip_dirs.get(ni, None)
        rot_rgb, rot_mask, four_corners = rotate_and_align(rgb_piece, mask_piece, tip_dir=tip_dir)

        filename = f"{finger_idx}_{FINGER_NAMES[finger_idx]}.png"
        save_rgba(os.path.join(sub, filename), rot_rgb, rot_mask)

        # 判定状态
        if is_merged:
            status = "粘连"
        elif ni >= len(nails):
            # 补甲产生的（索引超出原始U2Net检出数）
            status = "完好"
        else:
            status = "完好"

        # 四点坐标转为字符串
        corners_str = " ".join([f"{cx},{cy}" for cx, cy in four_corners])

        csv_rows.append({
            "文件名": f"{name}/{filename}",
            "手指编号": finger_idx,
            "素材状态": status,
            "转正后四点坐标": corners_str,
        })

        saved += 1

    print(f"  -> {saved}甲 粘连={has_merged}")

# ── 写入 CSV ──────────────────────────────────────
with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=["文件名", "手指编号", "素材状态", "转正后四点坐标"])
    writer.writeheader()
    writer.writerows(csv_rows)

print(f"\n完成! 输出目录: {OUT_DIR}")
print(f"CSV: {CSV_PATH}")