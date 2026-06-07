import cv2
import numpy as np
import os
import sys
from PIL import Image

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)
from core.nail_segmentor import NailSegmentor
import mediapipe as mp

CUT_DIR = os.path.join(_PROJ_ROOT, "assets", "cut_nail_png")
RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")

K3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
seg = NailSegmentor(mode="auto")
hands = mp.solutions.hands.Hands(static_image_mode=True, max_num_hands=2, min_detection_confidence=0.5)


def read_rgba(path):
    return np.array(Image.open(path).convert("RGBA"))


def save_rgba(path, arr):
    Image.fromarray(arr, "RGBA").save(path, "PNG")


def smooth_mask(mask):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    sm = np.zeros_like(mask)
    for c in cnts:
        if cv2.contourArea(c) > 10:
            cv2.drawContours(sm, [cv2.approxPolyDP(c, 0.4, True)], -1, 255, -1)
    return sm


def fill_edge_gaps(rgb, mask, kernel_size=3):
    """用甲面自身颜色填充mask边缘凹陷"""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = np.zeros_like(mask)
    for c in cnts:
        if cv2.contourArea(c) > 10:
            cv2.drawContours(hull, [cv2.convexHull(c)], -1, 255, -1)
    delta = cv2.subtract(hull, mask)
    delta = cv2.morphologyEx(delta, cv2.MORPH_CLOSE, K3, iterations=1)
    if cv2.countNonZero(delta) < 5:
        return mask, rgb
    inpainted = cv2.inpaint(rgb, delta, kernel_size, cv2.INPAINT_TELEA)
    result_rgb = rgb.copy()
    result_rgb[delta > 0] = inpainted[delta > 0]
    result_mask = cv2.bitwise_or(mask, delta)
    smoothed = smooth_mask(result_mask)
    return smoothed, result_rgb


# 需要填边缘缺失的：(商品, {甲片索引})
fill_targets = {
    "img_008": {2, 4},
    "img_009": {4},
    "img_010": {3, 4},
    "img_011": {0, 2},
}

# 钻石美甲：跳过轮廓平滑，保留凸起钻饰边缘锐利
DIAMOND_NAILS = {"img_020"}

print(f"优化 {CUT_DIR} ...\n")

for sub in sorted(os.listdir(CUT_DIR)):
    sp = os.path.join(CUT_DIR, sub)
    if not os.path.isdir(sp):
        continue
    pngs = sorted([f for f in os.listdir(sp) if f.endswith(".png")])
    if not pngs:
        continue

    nails = []

    for fname in pngs:
        fp = os.path.join(sp, fname)
        rgba = read_rgba(fp)
        if rgba is None:
            continue

        rgb = rgba[:, :, :3].copy()
        mask = rgba[:, :, 3].copy()
        idx = int(fname.replace(".png", ""))

        need_fill = fill_targets.get(sub, set())
        if idx in need_fill:
            mask, rgb = fill_edge_gaps(rgb, mask, kernel_size=3)
        elif sub in DIAMOND_NAILS:
            pass  # 钻石美甲跳过平滑，保留凸起钻饰边缘
        else:
            mask = smooth_mask(mask)

        nails.append(np.dstack([rgb, mask]))

    for f in os.listdir(sp):
        try: os.remove(os.path.join(sp, f))
        except: pass

    nails = nails[:5]
    for i, r in enumerate(nails):
        save_rgba(os.path.join(sp, f"{i}.png"), r)

    print(f"  {sub} -> {len(nails)}甲")


# MediaPipe补甲
print("\n[补甲]...")
for sub in sorted(os.listdir(CUT_DIR)):
    sp = os.path.join(CUT_DIR, sub)
    if not os.path.isdir(sp):
        continue
    pngs = [f for f in os.listdir(sp) if f.endswith(".png")]
    if len(pngs) >= 5:
        continue

    bgr = cv2.imread(os.path.join(RAW_DIR, sub + ".webp"))
    if bgr is None:
        continue
    rgb_full = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb_full.shape[:2]

    tips = hands.process(rgb_full)
    if not tips or not tips.multi_hand_landmarks or seg.model is None:
        continue

    existing = []
    for fname in pngs:
        r = read_rgba(os.path.join(sp, fname))
        if r is not None:
            existing.append(r)

    used = []
    for e in existing:
        mm = e[:, :, 3]
        ys, xs = np.where(mm > 0)
        if len(xs):
            used.append((xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min()))

    missing = 5 - len(existing)
    for hl in tips.multi_hand_landmarks:
        for idx in [4, 8, 12, 16, 20]:
            if missing <= 0:
                break
            lm = hl.landmark[idx]
            fx, fy = int(lm.x * w), int(lm.y * h)
            bx = max(0, fx - 30)
            by = max(0, fy - 30)
            bw = min(60, w - bx)
            bh = min(80, h - by)
            if bw < 15 or bh < 15:
                continue
            if any(bx < ux+uw and bx+bw > ux and by < uy+uh and by+bh > uy for ux, uy, uw, uh in used):
                continue
            crop_rgb = rgb_full[by:by+bh, bx:bx+bw]
            hc, wc = crop_rgb.shape[:2]
            inp = cv2.resize(crop_rgb, (512, 512)).astype(np.float32) / 127.5 - 1.0
            inp = inp.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
            out = seg.model.run([seg.output_name], {seg.input_name: inp})[0]
            prob = cv2.resize(out[0, 0], (wc, hc), interpolation=cv2.INTER_LINEAR)
            _, lm = cv2.threshold(prob, 0.08, 255, cv2.THRESH_BINARY)
            lm = lm.astype(np.uint8)
            if cv2.countNonZero(lm) < 15:
                continue
            if sub not in DIAMOND_NAILS:
                lm = smooth_mask(lm)
            existing.append(np.dstack([crop_rgb, lm]))
            used.append((bx, by, bw, bh))
            missing -= 1

    if len(existing) > len(pngs):
        for f in os.listdir(sp):
            try: os.remove(os.path.join(sp, f))
            except: pass
        for i, r in enumerate(existing[:5]):
            save_rgba(os.path.join(sp, f"{i}.png"), r)
        print(f"  {sub} -> {len(existing[:5])}甲")

print(f"\n完成! {CUT_DIR}")
