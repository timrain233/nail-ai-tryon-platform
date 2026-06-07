"""
auto_optimizer.py - AI自动优化引擎
全自动修复甲片抠图质量 + 四点贴合偏移
包含安全监控、自动备份、配置开关
"""
import os, csv, json, shutil, time, math, sys
from collections import defaultdict

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)

RECORDS_CSV = os.path.join(_PROJ_ROOT, "database", "tryon_records.csv")
POINTS_CSV = os.path.join(_PROJ_ROOT, "assets", "nail_cut3", "nail_points.csv")
RAW_DIR = os.path.join(_PROJ_ROOT, "assets", "raw_images")
CUT3_DIR = os.path.join(_PROJ_ROOT, "assets", "nail_cut3")
REPORTS_DIR = os.path.join(_PROJ_ROOT, "database", "ai_reports")
BACKUP_DIR = os.path.join(_PROJ_ROOT, "assets", "backup")
CONFIG_PATH = os.path.join(_PROJ_ROOT, "database", "config.json")
LOG_PATH = os.path.join(_PROJ_ROOT, "database", "auto_optimize_log.csv")

FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]


# ── 配置读写 ──

def _load_config():
    default = {
        "auto_optimize_enabled": True,
        "max_products_per_run": 3,
        "point_adjust_step": 1.0,
        "backup_dir": "assets/backup",
    }
    if not os.path.exists(CONFIG_PATH):
        return default
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k in default:
            cfg.setdefault(k, default[k])
        return cfg
    except Exception:
        return default


def _safe_read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return default


# ── 备份 ──

def _backup_file(src_path):
    if not os.path.exists(src_path):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rel = os.path.relpath(src_path, _PROJ_ROOT)
    safe_name = rel.replace(os.sep, "_")
    dst = os.path.join(BACKUP_DIR, f"{safe_name}.{ts}")
    try:
        shutil.copy2(src_path, dst)
        return dst
    except Exception:
        return None


# ── 优化日志 ──

_LOG_HEADER = ["time", "product_id", "action", "before_val", "after_val", "detail", "status"]

def _init_log():
    if not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) == 0:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        try:
            with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(_LOG_HEADER)
        except Exception:
            pass

def _write_log(product_id, action, before_val, after_val, detail, status="OK"):
    try:
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), product_id, action,
                        str(before_val), str(after_val), detail, status])
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# 类型A：抠图质量优化 → 重新跑U2Net+SAM生成透明甲片
# ═══════════════════════════════════════════════════════════

def _reprocess_product(product_id):
    folder = f"img_{str(product_id).zfill(3)}"
    raw_path = None
    for ext in [".webp", ".jpg", ".jpeg", ".png"]:
        p = os.path.join(RAW_DIR, f"{folder}{ext}")
        if os.path.exists(p):
            raw_path = p
            break
    if not raw_path:
        return False, "原图不存在"

    out_dir = os.path.join(CUT3_DIR, folder)
    os.makedirs(out_dir, exist_ok=True)

    import cv2
    import numpy as np
    from PIL import Image

    bgr = cv2.imread(raw_path)
    if bgr is None:
        return False, "图片读取失败"
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]

    try:
        from core.nail_segmentor import NailSegmentor
        seg = NailSegmentor(mode="auto")
        seg.predict_full(rgb)
        u2net_mask = seg._full_mask
        if u2net_mask is None or cv2.countNonZero(u2net_mask) == 0:
            return False, "U2Net无掩码"
        u2net_mask = u2net_mask.copy()
    except Exception as e:
        return False, f"U2Net失败: {e}"

    try:
        from core.nail_cut3 import split_nails_from_mask, get_fingertips, \
            refine_with_sam, rotate_and_align, save_rgba, find_finger_order
    except ImportError:
        try:
            sys.path.insert(0, _PROJ_ROOT)
            from core.nail_cut3 import split_nails_from_mask, get_fingertips, \
                refine_with_sam, rotate_and_align, save_rgba, find_finger_order
        except Exception as e:
            return False, f"导入nail_cut3失败: {e}"

    try:
        nails, has_merged = split_nails_from_mask(rgb, u2net_mask)
        if not nails:
            return False, "无有效指甲"
    except Exception as e:
        return False, f"split_nails失败: {e}"

    fingertips = get_fingertips(rgb)

    enhanced = []
    for nail in nails:
        x, y, bw, bh = nail["bbox"]
        u2net_local = nail["mask"]
        try:
            rm = refine_with_sam(rgb, (x, y, bw, bh), u2net_local)
        except Exception:
            rm = u2net_local
        ys, xs = np.where(rm > 0)
        if len(xs) == 0:
            continue
        nx, ny = xs.min(), ys.min()
        nw, nh = xs.max() - nx + 1, ys.max() - ny + 1
        enhanced.append({
            "rgb": rgb[ny:ny+nh, nx:nx+nw].copy(),
            "mask": rm[ny:ny+nh, nx:nx+nw],
            "bbox": (x, y, bw, bh),
            "crop_bbox": (nx, ny, nw, nh),
            "is_merged": nail.get("is_merged", False),
        })

    if not enhanced:
        return False, "SAM后无有效指甲"

    order = find_finger_order([n["bbox"] for n in enhanced], fingertips)

    # 指尖方向
    nail_tip_dirs = {}
    if fingertips:
        for i, fi in enumerate(order[:5]):
            if fi < len(enhanced):
                b = enhanced[fi]["bbox"]
                cx = b[0] + b[2] // 2
                cy = b[1] + b[3] // 2
                if i < len(fingertips):
                    fx, fy = fingertips[i]
                    nail_tip_dirs[fi] = (fx - cx, fy - cy)

    points_rows = []
    success_count = 0
    for ordered_idx, fi in enumerate(order):
        if fi >= len(enhanced) or ordered_idx >= 5:
            break
        n = enhanced[fi]
        tip_dir = nail_tip_dirs.get(fi, None)
        try:
            rot_rgb, rot_mask, corners = rotate_and_align(n["rgb"], n["mask"], tip_dir)
            if rot_mask is None or cv2.countNonZero(rot_mask) < 30:
                continue
            fname = os.path.join(out_dir, f"{ordered_idx}_{FINGER_NAMES[ordered_idx]}.png")
            save_rgba(fname, rot_rgb, rot_mask)
            p1, p2, p3, p4 = corners
            points_rows.append({
                "商品ID": folder,
                "指头序号": str(ordered_idx),
                "p1": f"{p1[0]},{p1[1]}",
                "p2": f"{p2[0]},{p2[1]}",
                "p3": f"{p3[0]},{p3[1]}",
                "p4": f"{p4[0]},{p4[1]}",
            })
            success_count += 1
        except Exception:
            continue

    # 写入 nail_points.csv（覆盖该商品的旧四点）
    if points_rows:
        all_points = []
        if os.path.exists(POINTS_CSV):
            try:
                with open(POINTS_CSV, "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("商品ID", "").strip() != folder:
                            all_points.append(row)
            except Exception:
                pass
        all_points.extend(points_rows)
        _backup_file(POINTS_CSV)
        try:
            with open(POINTS_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["商品ID", "指头序号", "p1", "p2", "p3", "p4"])
                w.writeheader()
                w.writerows(all_points)
        except Exception as e:
            return False, f"写nail_points.csv失败: {e}"

    return True, f"成功重抠{success_count}指"


# ═══════════════════════════════════════════════════════════
# 类型B：贴合优化 → 微调四点坐标
# ═══════════════════════════════════════════════════════════

def _read_points_data():
    data = {}
    if not os.path.exists(POINTS_CSV):
        return data
    try:
        with open(POINTS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pid = (row.get("商品ID") or "").strip()
                try:
                    fid = int(row.get("指头序号", -1))
                except (ValueError, TypeError):
                    continue
                if not pid or fid < 0 or fid > 4:
                    continue
                pts = []
                for k in ["p1", "p2", "p3", "p4"]:
                    v = (row.get(k) or "").strip()
                    try:
                        x, y = v.split(",")
                        pts.append([int(x), int(y)])
                    except (ValueError, TypeError):
                        break
                if len(pts) == 4:
                    data.setdefault(pid, {})[fid] = pts
    except Exception:
        pass
    return data


def _adjust_points_for_fit(product_id, step=1.0):
    folder = f"img_{str(product_id).zfill(3)}"
    all_data = _read_points_data()
    if folder not in all_data:
        return False, "无四点数据"

    pts_dict = all_data[folder]
    modifications = []

    for fid in range(5):
        if fid not in pts_dict:
            continue
        pts = pts_dict[fid]
        # 计算当前矩形中心
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx = sum(xs) / 4.0
        cy = sum(ys) / 4.0
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)

        if w < 5 or h < 5:
            continue

        # 微调：整体向外扩2%使甲片略大于指甲，提高覆盖度
        scale = 1.0 + (step * 0.005)
        new_pts = []
        for p in pts:
            nx = int(round(cx + (p[0] - cx) * scale))
            ny = int(round(cy + (p[1] - cy) * scale))
            new_pts.append([nx, ny])
        modifications.append((fid, pts_dict[fid], new_pts))
        pts_dict[fid] = new_pts

    if not modifications:
        return False, "无需调整"

    _backup_file(POINTS_CSV)

    all_rows = []
    if os.path.exists(POINTS_CSV):
        try:
            seen = set()
            with open(POINTS_CSV, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    rpid = (row.get("商品ID") or "").strip()
                    try:
                        rfid = int(row.get("指头序号", -1))
                    except (ValueError, TypeError):
                        rfid = -1
                    key = (rpid, rfid)
                    if rpid == folder and rfid in [m[0] for m in modifications]:
                        if key not in seen:
                            seen.add(key)
                            f_pts = pts_dict.get(rfid)
                            if f_pts:
                                row["p1"] = f"{f_pts[0][0]},{f_pts[0][1]}"
                                row["p2"] = f"{f_pts[1][0]},{f_pts[1][1]}"
                                row["p3"] = f"{f_pts[2][0]},{f_pts[2][1]}"
                                row["p4"] = f"{f_pts[3][0]},{f_pts[3][1]}"
                                all_rows.append(row)
                            continue
                    all_rows.append(row)
        except Exception:
            all_rows = []

        # 确保所有修改行都写入
        for fid, _, new_pts in modifications:
            key = (folder, fid)
            if key not in seen:
                all_rows.append({
                    "商品ID": folder,
                    "指头序号": str(fid),
                    "p1": f"{new_pts[0][0]},{new_pts[0][1]}",
                    "p2": f"{new_pts[1][0]},{new_pts[1][1]}",
                    "p3": f"{new_pts[2][0]},{new_pts[2][1]}",
                    "p4": f"{new_pts[3][0]},{new_pts[3][1]}",
                })

        try:
            with open(POINTS_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["商品ID", "指头序号", "p1", "p2", "p3", "p4"])
                w.writeheader()
                w.writerows(all_rows)
        except Exception as e:
            return False, f"写入失败: {e}"

    detail = "; ".join([f"指{fid}: scale={1+(step*0.005):.3f}" for fid, _, _ in modifications])
    return True, detail


# ═══════════════════════════════════════════════════════════
# 评分重算
# ═══════════════════════════════════════════════════════════

def _recalc_quality(product_id):
    try:
        from core.nail_quality_check import get_quality_score, get_fit_score
        qs = get_quality_score(product_id)
        fs = get_fit_score([], product_id)
        return qs, fs
    except Exception:
        return None, None


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def run_optimization(dry_run=False):
    _init_log()
    config = _load_config()

    if not config.get("auto_optimize_enabled", True):
        print("[auto_optimizer] 全局开关已关闭，跳过优化")
        return

    max_products = config.get("max_products_per_run", 3)
    step = config.get("point_adjust_step", 1.0)

    print("[auto_optimizer] 读取劣质甲片清单...")

    bad_rows = _safe_read_csv(os.path.join(REPORTS_DIR, "ai_bad_nails.csv"))
    opt_rows = _safe_read_csv(os.path.join(REPORTS_DIR, "ai_optimize_suggest.csv"))

    if not bad_rows and not opt_rows:
        print("[auto_optimizer] 无待优化商品")
        return

    candidates = set()
    for br in bad_rows:
        pid = (br.get("product_id") or "").strip()
        if pid:
            candidates.add(pid)
    for otr in opt_rows:
        pid = (otr.get("product_id") or "").strip()
        if pid:
            candidates.add(pid)

    # 按优先级排序：quality最低优先
    sorted_candidates = sorted(candidates, key=lambda p: (
        _safe_float({br["product_id"]: br.get("avg_quality", "100") for br in bad_rows}.get(p, "100"))
    ))

    products_to_fix = sorted_candidates[:max_products]
    print(f"[auto_optimizer] 本次处理 {len(products_to_fix)} 个商品: {products_to_fix}")

    for pid in products_to_fix:
        if dry_run:
            print(f"[auto_optimizer] [DRY-RUN] 商品{pid}: 跳过实际修改")
            continue

        print(f"[auto_optimizer] 处理商品 {pid}...")
        q_before, f_before = _recalc_quality(pid)

        action_taken = None
        result_ok = False
        detail = ""

        # 判断问题类型
        bad_info = None
        for br in bad_rows:
            if (br.get("product_id") or "").strip() == pid:
                bad_info = br
                break
        opt_info = None
        for otr in opt_rows:
            if (otr.get("product_id") or "").strip() == pid:
                opt_info = otr
                break

        reason = (bad_info.get("bad_reason", "") if bad_info else "") or \
                 (opt_info.get("suggest_type", "") if opt_info else "")

        if "质量" in reason or "抠图" in reason:
            print(f"[auto_optimizer]   类型A: 重抠图")
            result_ok, detail = _reprocess_product(pid)
            action_taken = "reprocess_cutout"
        elif "贴合" in reason or "四点" in reason:
            print(f"[auto_optimizer]   类型B: 微调四点")
            result_ok, detail = _adjust_points_for_fit(pid, step)
            action_taken = "adjust_points"
        else:
            # 默认：先尝试四点微调（安全操作）
            print(f"[auto_optimizer]   默认: 微调四点")
            result_ok, detail = _adjust_points_for_fit(pid, step)
            action_taken = "adjust_points"

        q_after, f_after = _recalc_quality(pid)

        status = "OK" if result_ok else "FAIL"
        log_detail = f"{detail} | q:{q_before}->{q_after} f:{f_before}->{f_after}" if q_after else detail
        _write_log(pid, action_taken,
                   f"q={q_before},f={f_before}",
                   f"q={q_after},f={f_after}",
                   log_detail, status)
        print(f"[auto_optimizer]   结果: {status} | {log_detail}")

    print("[auto_optimizer] 优化完成")
    return products_to_fix


if __name__ == "__main__":
    run_optimization()
