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

RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")
OUT_DIR = os.path.join(_PROJ_ROOT, "assets", "cut_nail_png")

# 个别商品的特殊参数配置
SPECIAL_CONFIG = {
    "img_020": {  # 20号带钻饰美甲: 保留钻石小连通块并入主甲
        "diamond_mode": True,
    },
}

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
                       "bbox": (x, y, bw, bh), "area": area})
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
    """用PIL保存RGBA PNG，避免OpenCV通道交换问题"""
    rgba = np.dstack([rgb_np, mask_np])
    img = Image.fromarray(rgba, "RGBA")
    img.save(path, "PNG")


def extract_nails_diamond_mode(rgb, full_mask):
    """钻石美甲专用：保留U2Net原生mask+小钻块(距离<8px)并入主甲+补甲"""
    h, w = rgb.shape[:2]
    tp = h * w

    # 1. U2Net原生mask保留，取消小噪点清除
    # 2. 仅(1,1)极小核闭运算，禁用腐蚀
    K1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (1, 1))
    mask = cv2.morphologyEx(full_mask, cv2.MORPH_CLOSE, K1)

    # 连通域分析
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    min_a = tp * 0.0003  # 主甲面积下限
    max_a = tp * 0.40

    main_idx = []
    small_pieces = []  # 小连通块（侧边钻候选）
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < 5:
            continue
        if min_a <= a <= max_a:
            main_idx.append(i)
        elif a < min_a:
            small_pieces.append(i)

    # 全局函数：把小块合并到主甲
    def merge_small_to_nail(nail_label, nail_mask_full):
        """将距离<8px的小块并入nails的mask"""
        K8 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (8, 8))
        result = nail_mask_full.copy()
        for si in small_pieces:
            sm = (labels == si).astype(np.uint8)
            dilated = cv2.dilate(sm, K8)
            if cv2.countNonZero(cv2.bitwise_and(dilated, result)) > 0:
                result = cv2.bitwise_or(result, sm)
        return result

    def crop_nail(nail_mask_full):
        """从全尺寸mask裁剪出最小bbox"""
        ys, xs = np.where(nail_mask_full > 0)
        if len(xs) == 0:
            return None
        x, y = xs.min(), ys.min()
        bw = xs.max() - x + 1
        bh = ys.max() - y + 1
        return {
            "mask": nail_mask_full[y:y+bh, x:x+bw],
            "rgb": rgb[y:y+bh, x:x+bw].copy(),
            "bbox": (x, y, bw, bh),
            "area": np.count_nonzero(nail_mask_full),
        }

    nails = []

    # 提取主甲（含小钻合并）
    for mi in main_idx:
        base = (labels == mi).astype(np.uint8) * 255
        enriched = merge_small_to_nail(mi, base)
        n2 = crop_nail(enriched)
        if n2:
            nails.append(n2)

    # 3. 不够5甲则补甲，补甲后也做钻块合并
    if len(nails) < 5 and segmentor.model is not None:
        missing = 5 - len(nails)
        tips = get_fingertips(rgb)
        if tips:
            used = [n["bbox"] for n in nails]
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

                # 补甲也做钻块合并：在全尺寸上创建补甲mask
                pad_full = np.zeros((h, w), dtype=np.uint8)
                pad_full[by:by+bh, bx:bx+bw] = lm
                enriched = merge_small_to_nail(None, pad_full)
                n2 = crop_nail(enriched)
                if n2:
                    nails.append(n2)
                    used.append((bx, by, bw, bh))
                    missing -= 1

    # 按面积排序取前5
    nails.sort(key=lambda n: n["area"], reverse=True)
    return nails[:5] if nails else None


paths = sorted(glob.glob(os.path.join(RAW_DIR, "*")))
print(f"共 {len(paths)} 张\n")

for p in paths:
    fname = os.path.basename(p)
    name = os.path.splitext(fname)[0]
    bgr = cv2.imread(p)
    if bgr is None:
        continue

    # BGR → RGB (显示颜色)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    tp = h * w

    # 读取特殊配置
    cfg = SPECIAL_CONFIG.get(name, {})

    mask = get_mask(rgb)
    if mask is None:
        print(f"  {fname} -> 无掩码")
        continue

    # 钻石美甲模式：保留U2Net原生mask+侧边钻块8px并入主甲
    if cfg.get("diamond_mode"):
        final = extract_nails_diamond_mode(rgb, mask)
        if final is None:
            final = []
        print(f"  {fname} -> {len(final)}甲 (钻石模式)")
        sub = os.path.join(OUT_DIR, name)
        os.makedirs(sub, exist_ok=True)
        for i, n in enumerate(final):
            m = n["mask"]
            r = n["rgb"]
            if r.shape[:2] != m.shape[:2]:
                m = cv2.resize(m, (r.shape[1], r.shape[0]), interpolation=cv2.INTER_NEAREST)
            save_rgba_pil(os.path.join(sub, f"{i}.png"), r, m)
        continue

    nails = split_nails(rgb, mask, min_area_ratio=cfg.get("min_area_ratio", 0.0003), max_area_ratio=cfg.get("max_area_ratio", 0.40))
    nails.sort(key=lambda n: n["area"], reverse=True)

    if len(nails) > 5:
        nails = nails[:5]

    final = list(nails)

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
