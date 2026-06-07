import cv2
import numpy as np
import os
import glob
import sys
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nail_segmentor import NailSegmentor
import mediapipe as mp

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw_images")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nail_cut2")

# 清空输出目录
os.makedirs(OUT_DIR, exist_ok=True)
for sub in os.listdir(OUT_DIR):
    sp = os.path.join(OUT_DIR, sub)
    if os.path.isdir(sp):
        for f in os.listdir(sp):
            try: os.remove(os.path.join(sp, f))
            except: pass
        try: os.rmdir(sp)
        except: pass
    else:
        try: os.remove(sp)
        except: pass

segmentor = NailSegmentor(mode="auto")
print(f"分割器: {segmentor.mode}")

mp_hands = mp.solutions.hands
hands = mp_hands.Hands(static_image_mode=True, max_num_hands=2, min_detection_confidence=0.5)


def get_mask(rgb):
    segmentor.predict_full(rgb)
    m = segmentor._full_mask
    if m is None or cv2.countNonZero(m) == 0:
        return None
    return m.copy()


def split_nails(rgb, mask, min_area_ratio=0.0003, max_area_ratio=0.40):
    h, w = rgb.shape[:2]
    tp = h * w
    min_a = tp * min_area_ratio
    max_a = tp * max_area_ratio
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    nails = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_a or area > max_a:
            continue
        cm = (labels == i).astype(np.uint8) * 255
        nails.append({"mask": cm[y:y+bh, x:x+bw], "rgb": rgb[y:y+bh, x:x+bw].copy(),
                       "bbox": (x, y, bw, bh), "area": area, "label": i})
    return nails


def get_fingertips(rgb):
    results = hands.process(rgb)
    if not results.multi_hand_landmarks:
        return None
    h, w = rgb.shape[:2]
    pts = []
    for hl in results.multi_hand_landmarks:
        for idx in [4, 8, 12, 16, 20]:
            lm = hl.landmark[idx]
            pts.append((int(lm.x * w), int(lm.y * h)))
    return pts


def save_rgba_pil(path, rgb_np, mask_np):
    rgba = np.dstack([rgb_np, mask_np])
    img = Image.fromarray(rgba, "RGBA")
    img.save(path, "PNG")


def merge_small_to_nails(raw_mask, nails, max_dist=10):
    """钻饰/闪粉连通域合并规则：
    任意小色块(钻/闪粉)中心点距离最近主甲轮廓<max_dist像素，自动融合并入所属指甲单体"""
    if not nails:
        return nails

    h, w = raw_mask.shape[:2]
    # 从raw_mask获取全部连通域
    n, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, connectivity=8)

    tp = h * w
    min_a = tp * 0.0003  # 与split_nails一致
    max_a = tp * 0.40

    # 分类：主甲块 vs 小色块（钻/闪粉候选）
    main_mask_full = np.zeros((h, w), dtype=np.uint8)
    small_pieces = []

    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < 5:
            continue
        x, y, bw, bh, _ = stats[i]
        if min_a <= a <= max_a:
            # 主甲块，累加到全图主甲mask
            main_mask_full[labels == i] = 255
        elif a < min_a:
            small_pieces.append({
                "label": i,
                "area": a,
                "bbox": (x, y, bw, bh),
            })

    if not small_pieces:
        return nails

    # 构建每个主甲的轮廓
    nail_contours = {}
    for nail in nails:
        bx, by, bw, bh = nail["bbox"]
        # 在全尺寸上重建nail的mask
        full_nail = np.zeros((h, w), dtype=np.uint8)
        full_nail[by:by+bh, bx:bx+bw] = nail["mask"]
        cnts, _ = cv2.findContours(full_nail, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            # 展平为(N,2)格式供pointPolygonTest使用
            pts = cnts[0].reshape(-1, 2).astype(np.float32)
            nail_contours[nail["label"]] = pts

    if not nail_contours:
        return nails

    # 对每个小色块，检查到最近主甲轮廓的距离
    merge_map = {}  # small_label -> main_label
    for sp in small_pieces:
        # 小块中心点（转为float供pointPolygonTest使用）
        sx, sy, sw, sh = sp["bbox"]
        cx = float(sx + sw // 2)
        cy = float(sy + sh // 2)

        best_main = None
        best_dist = float(max_dist)

        for label, pts in nail_contours.items():
            d = cv2.pointPolygonTest(pts, (cx, cy), True)
            dist = abs(d)
            if dist < best_dist:
                best_dist = dist
                best_main = label

        if best_main is not None:
            merge_map[sp["label"]] = best_main

    if not merge_map:
        return nails

    # 执行合并：将小色块并入对应主甲的mask
    for nail in nails:
        if nail["label"] in merge_map.values():
            # 找到所有合并到这个主甲的小块
            bx, by, bw, bh = nail["bbox"]
            # 在全尺寸mask上合并
            full_nail = np.zeros((h, w), dtype=np.uint8)
            full_nail[by:by+bh, bx:bx+bw] = nail["mask"]

            for small_lbl, main_lbl in merge_map.items():
                if main_lbl == nail["label"]:
                    full_nail[labels == small_lbl] = 255

            # 重新裁剪
            ys, xs = np.where(full_nail > 0)
            if len(xs) == 0:
                continue
            nx, ny = xs.min(), ys.min()
            nw = xs.max() - nx + 1
            nh = ys.max() - ny + 1
            nail["mask"] = full_nail[ny:ny+nh, nx:nx+nw]
            nail["rgb"] = rgb[ny:ny+nh, nx:nx+nw].copy()
            nail["bbox"] = (nx, ny, nw, nh)
            nail["area"] = np.count_nonzero(full_nail)

    return nails


paths = sorted(glob.glob(os.path.join(RAW_DIR, "*")))
print(f"共 {len(paths)} 张\n")

for p in paths:
    fname = os.path.basename(p)
    name = os.path.splitext(fname)[0]
    bgr = cv2.imread(p)
    if bgr is None:
        continue

    # BGR -> RGB
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    tp = h * w

    # 1. U2Net原生掩码
    raw_mask = get_mask(rgb)
    if raw_mask is None:
        print(f"  {fname} -> 无掩码")
        continue

    # 2. 仅(1,1)闭运算，无腐蚀
    K1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (1, 1))
    mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, K1)

    # 3. 标准分甲（面积/长宽比阈值不变）
    nails = split_nails(rgb, mask, min_area_ratio=0.0003, max_area_ratio=0.40)
    nails.sort(key=lambda n: n["area"], reverse=True)

    if len(nails) > 5:
        nails = nails[:5]

    # 4. 钻饰/闪粉连通域合并
    nails = merge_small_to_nails(raw_mask, nails, max_dist=10)

    final = list(nails)

    # 5. 补甲（<=5甲）
    if len(final) < 5 and segmentor.model is not None:
        missing = 5 - len(final)
        tips = get_fingertips(rgb)
        if tips:
            used = [n["bbox"] for n in final]
            for fx, fy in tips:
                if missing <= 0:
                    break
                bx = max(0, fx - 20)
                by = max(0, fy - 20)
                bw = min(40, w - bx)
                bh = min(60, h - by)
                if bw < 10 or bh < 10:
                    continue
                overlap = any(bx < ux+uw and bx+bw > ux and by < uy+uh and by+bh > uy for ux, uy, uw, uh in used)
                if overlap:
                    continue
                crop = rgb[by:by+bh, bx:bx+bw]
                hc, wc = crop.shape[:2]
                inp = cv2.resize(crop, (512, 512)).astype(np.float32) / 127.5 - 1.0
                inp = inp.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
                out = segmentor.model.run([segmentor.output_name], {segmentor.input_name: inp})[0]
                prob = cv2.resize(out[0, 0], (wc, hc), interpolation=cv2.INTER_LINEAR)
                _, lm = cv2.threshold(prob, 0.2, 255, cv2.THRESH_BINARY)
                lm = lm.astype(np.uint8)
                la = cv2.countNonZero(lm)
                if la < tp * 0.0002 or la > tp * 0.40:
                    continue
                final.append({"mask": lm, "rgb": crop.copy(),
                              "bbox": (bx, by, bw, bh), "area": la})
                used.append((bx, by, bw, bh))
                missing -= 1

    # 保存
    sub = os.path.join(OUT_DIR, name)
    os.makedirs(sub, exist_ok=True)
    for i, n in enumerate(final):
        m = n["mask"]
        r = n["rgb"]
        if r.shape[:2] != m.shape[:2]:
            m = cv2.resize(m, (r.shape[1], r.shape[0]), interpolation=cv2.INTER_NEAREST)
        save_rgba_pil(os.path.join(sub, f"{i}.png"), r, m)

    print(f"  {fname} -> {len(final)}甲")

print(f"\n完成! {OUT_DIR}")