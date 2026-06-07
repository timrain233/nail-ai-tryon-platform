"""
U2Net + SAM 联合美甲抠图系统
- U2Net: 主体甲面分割
- SAM: 钻石/闪粉/边缘细节补全
- MediaPipe: 手部5指排序
- 自动旋转矫正: 甲尖朝上
"""
import cv2
import numpy as np
import os
import glob
import sys
from PIL import Image

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)
from core.nail_segmentor import NailSegmentor
import mediapipe as mp

# ── 路径 ──────────────────────────────────────────
RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")
OUT_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_cuts")
SAM_CKPT = os.path.join(_PROJ_ROOT, "models", "sam_vit_b_01ec64.pth")

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

# ── 文件名映射 ────────────────────────────────────
FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]
FINGER_IDS = [4, 8, 12, 16, 20]  # MediaPipe指尖索引


def get_fingertips(rgb):
    """获取所有手指尖坐标，返回 [(x, y, finger_id), ...]"""
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
    """U2Net全图推理，返回原始mask"""
    segmentor.predict_full(rgb)
    m = segmentor._full_mask
    if m is None or cv2.countNonZero(m) == 0:
        return None
    return m.copy()


def split_nails_from_mask(rgb, full_mask):
    """从U2Net全图mask中提取5个指甲区域"""
    h, w = full_mask.shape[:2]
    tp = h * w
    min_a = tp * 0.0003
    max_a = tp * 0.40

    n, labels, stats, _ = cv2.connectedComponentsWithStats(full_mask, connectivity=8)
    nails = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_a or area > max_a:
            continue
        cm = (labels == i).astype(np.uint8) * 255
        # 裁剪出局部mask
        local_mask = cm[y:y+bh, x:x+bw]
        nails.append({
            "mask": local_mask,  # 局部mask
            "bbox": (x, y, bw, bh),
            "area": area,
            "label": i,
        })
    nails.sort(key=lambda n: n["area"], reverse=True)
    return nails[:5]


def refine_with_sam(rgb, box, u2net_mask_local):
    """用SAM精修指甲区域：box prompt + U2Net mask作为参考"""
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

    # 调整box到裁剪坐标
    box_crop = np.array([x - x1, y - y1, x + bw - x1, y + bh - y1])

    # SAM推理
    predictor.set_image(crop)
    masks, scores, _ = predictor.predict(
        box=box_crop[None, :],
        multimask_output=True,
    )

    # 选择与U2Net mask重叠最多的SAM mask
    best_sam = None
    best_iou = 0
    # u2net_mask_local在裁剪图中的位置
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

    # OR融合: U2Net全图mask | SAM全图mask
    full_mask = np.zeros((rgb.shape[0], rgb.shape[1]), dtype=np.uint8)
    # U2Net原始mask
    full_mask[y:y+bh, x:x+bw] = u2net_mask_local
    # SAM mask覆盖
    full_mask[y1:y2, x1:x2] = cv2.bitwise_or(
        full_mask[y1:y2, x1:x2],
        best_sam
    )

    return full_mask


def rotate_nail_tip_up(rgb, mask):
    """旋转指甲使甲尖朝上，甲根朝下"""
    h, w = mask.shape[:2]
    if h < 10 or w < 10:
        return rgb, mask

    # 用PCA找主方向
    ys, xs = np.where(mask > 0)
    if len(xs) < 10:
        return rgb, mask

    mean_x, mean_y = np.mean(xs), np.mean(ys)
    cov = np.cov(xs - mean_x, ys - mean_y)
    eigenvalues, eigenvectors = np.linalg.eig(cov)
    idx = np.argmax(eigenvalues)
    main_axis = eigenvectors[:, idx]  # (dx, dy)

    # 计算需要旋转的角度：使主方向对齐垂直(y轴)
    angle = np.degrees(np.arctan2(main_axis[0], main_axis[1]))

    # 判断甲尖方向：mask上半部分像素更多 → 甲尖朝上
    # 先旋转看看
    rotated = cv2.rotate(rgb, cv2.ROTATE_90_CLOCKWISE)  # placeholder
    # 实际旋转
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

    # 检测甲尖方向：mask在旋转后的上半部分和下半部分哪个更"尖"
    top_half = rot_mask[:nh//2, :]
    bot_half = rot_mask[nh//2:, :]
    top_px = cv2.countNonZero(top_half)
    bot_px = cv2.countNonZero(bot_half)

    if top_px < bot_px:
        # 甲尖在下方，翻转180度
        rot_rgb = cv2.rotate(rot_rgb, cv2.ROTATE_180)
        rot_mask = cv2.rotate(rot_mask, cv2.ROTATE_180)

    return rot_rgb, rot_mask


def find_finger_order(nail_bboxes, fingertips):
    """将指甲按MediaPipe指尖位置排序: 0=拇指,1=食指,2=中指,3=无名指,4=小指"""
    if len(fingertips) < 5 or len(nail_bboxes) < 5:
        return list(range(len(nail_bboxes)))  # 无法排序，保持原序

    # 为每个指甲bbox找到最近的指尖
    assignments = []
    for i, (x, y, bw, bh) in enumerate(nail_bboxes):
        cx = x + bw // 2
        cy = y + bh // 2
        best_f = -1
        best_d = 1e9
        for fi, (fx, fy) in enumerate(fingertips):
            d = (cx - fx) ** 2 + (cy - fy) ** 2
            if d < best_d:
                best_d = d
                best_f = fi
        assignments.append((best_f, i))

    # 5个指尖按0(拇指)到4(小指)排列
    # 假设手在图片中大致水平，拇指在左，小指在右
    # 按x坐标排序指尖
    sorted_fingers = sorted(range(len(fingertips)), key=lambda f: fingertips[f][0])

    # 将指甲映射到手指索引
    finger_to_nail = {}
    for fi, ni in assignments:
        # 找最近的指尖
        nearest_f = -1
        nearest_d = 1e9
        nx = nail_bboxes[ni][0] + nail_bboxes[ni][2] // 2
        ny = nail_bboxes[ni][1] + nail_bboxes[ni][3] // 2
        for fi2 in range(5):
            fy, fx = fingertips[fi2]
            d = (nx - fx) ** 2 + (ny - fy) ** 2
            if d < nearest_d:
                nearest_d = d
                nearest_f = fi2
        finger_to_nail[nearest_f] = ni

    # 按手指顺序排列: 0=thumb, 1=index, 2=middle, 3=ring, 4=pinky
    ordered = []
    for finger_idx in range(5):
        if finger_idx in finger_to_nail:
            ordered.append(finger_to_nail[finger_idx])
        else:
            # 找一个未分配的
            for ni in range(len(nail_bboxes)):
                if ni not in ordered:
                    ordered.append(ni)
                    break

    return ordered


def save_rgba(path, rgb, mask):
    """保存RGBA PNG"""
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

    # 1. U2Net 主体甲面分割
    u2net_mask = get_u2net_mask(rgb)
    if u2net_mask is None:
        print(f"  U2Net无掩码")
        continue

    # 2. 提取指甲区域
    nails = split_nails_from_mask(rgb, u2net_mask)
    if not nails:
        print(f"  无有效指甲")
        continue

    # 3. MediaPipe指尖检测
    fingertips = get_fingertips(rgb)

    # 4. SAM精修每个指甲
    enhanced_nails = []
    for nail in nails:
        x, y, bw, bh = nail["bbox"]
        # 提取U2Net局部mask
        u2net_local = nail["mask"]

        # SAM精修
        refined_mask = refine_with_sam(rgb, (x, y, bw, bh), u2net_local)

        # 从全图mask中裁剪出最终的指甲
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
        })

    # 5. MediaPipe补甲
    if len(enhanced_nails) < 5 and segmentor.model is not None and fingertips:
        # 构建现有指甲的mask（全尺寸），用于检查指尖是否已被覆盖
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

            # 检查指尖是否已被现有mask覆盖（距离<5px）
            bx = max(0, fx - 35)
            by = max(0, fy - 35)
            bw = min(70, w - bx)
            bh = min(90, h - by)
            if bw < 15 or bh < 15:
                continue

            # 如果指尖在现有mask内，跳过
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
            })
            # 更新existing_masks
            existing_masks[by:by+bh, bx:bx+bw] = cv2.bitwise_or(
                existing_masks[by:by+bh, bx:bx+bw],
                cv2.resize(lm, (bw, bh), interpolation=cv2.INTER_NEAREST)
            )

    # 6. 手指排序
    nail_bboxes = [(n["crop_bbox"][0], n["crop_bbox"][1],
                    n["crop_bbox"][2], n["crop_bbox"][3]) for n in enhanced_nails]
    order = find_finger_order(nail_bboxes, fingertips)

    # 7. 保存
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

        if rgb_piece.shape[:2] != mask_piece.shape[:2]:
            mask_piece = cv2.resize(mask_piece, (rgb_piece.shape[1], rgb_piece.shape[0]),
                                     interpolation=cv2.INTER_NEAREST)

        # 旋转矫正
        rot_rgb, rot_mask = rotate_nail_tip_up(rgb_piece, mask_piece)

        filename = f"{finger_idx}_{FINGER_NAMES[finger_idx]}.png"
        save_rgba(os.path.join(sub, filename), rot_rgb, rot_mask)
        saved += 1

    print(f"  -> {saved}甲")

print(f"\n完成! 输出目录: {OUT_DIR}")