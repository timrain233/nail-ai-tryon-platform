"""
ai_analyzer.py - 自动化数据分析与报表生成
纯本地计算，不调外部大模型，从 tryon_records + 行为日志 读取数据
"""
import os, csv, sys
from collections import defaultdict

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

RECORDS_CSV = os.path.join(_PROJ_ROOT, "database", "tryon_records.csv")
BH_LOG = os.path.join(_PROJ_ROOT, "nail_database", "tryon_behavior_log.csv")
PRODUCT_CSV = os.path.join(_PROJ_ROOT, "nail_database", "nail_product2.csv")
REPORTS_DIR = os.path.join(_PROJ_ROOT, "database", "ai_reports")


# ── 工具函数 ──

def _safe_read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[ai_analyzer]   -> {path} ({len(rows)} rows)")
    except Exception as e:
        print(f"[ai_analyzer]   -> {path} FAILED: {e}")


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return default


def _pname(pid):
    return f"img_{str(pid).zfill(3)}.webp"


# ── 报表生成 ──

def generate_all():
    print(f"[ai_analyzer] 开始分析，数据源: {RECORDS_CSV}")
    records = _safe_read_csv(RECORDS_CSV)
    bh_rows = _safe_read_csv(BH_LOG)
    products = _safe_read_csv(PRODUCT_CSV)

    if not records:
        print("[ai_analyzer] 无试戴记录，生成空报表")
        _write_empty_reports()
        return
    print(f"[ai_analyzer] 试戴记录: {len(records)}, 行为日志: {len(bh_rows)}, 商品: {len(products)}")

    # ── 按商品聚合 ──
    prod_stats = defaultdict(lambda: {
        "tryon_count": 0, "fit_sum": 0.0, "quality_sum": 0.0, "fit_n": 0, "quality_n": 0
    })

    for r in records:
        pid = (r.get("product_id") or "").strip()
        if not pid:
            continue
        s = prod_stats[pid]
        s["tryon_count"] += 1
        fs = _safe_float(r.get("fit_score"))
        qs = _safe_float(r.get("quality_score"))
        if fs:
            s["fit_sum"] += fs
            s["fit_n"] += 1
        if qs:
            s["quality_sum"] += qs
            s["quality_n"] += 1

    # ── 按用户聚合 ──
    user_stats = defaultdict(lambda: {"count": 0, "products": set()})
    for r in records:
        uid = (r.get("uid") or "").strip()
        pid = (r.get("product_id") or "").strip()
        if not uid:
            continue
        user_stats[uid]["count"] += 1
        if pid:
            user_stats[uid]["products"].add(pid)

    # ── fav_count: 从行为日志统计 ──
    fav_count = defaultdict(int)
    for br in bh_rows:
        action = (br.get("action") or "").strip()
        pid = (br.get("product_id") or "").strip()
        if action == "favorite" and pid:
            fav_count[pid] += 1

    # ── 商品信息映射 ──
    prod_name = {}
    for p in products:
        pid = (p.get("item_id") or "").strip()
        if pid:
            prod_name[pid] = p.get("item_name", "")

    # ═══════════ 报表1：ai_product_sort.csv ═══════════
    sort_rows = []
    for pid, s in prod_stats.items():
        avg_fit = round(s["fit_sum"] / s["fit_n"], 1) if s["fit_n"] > 0 else 0.0
        avg_quality = round(s["quality_sum"] / s["quality_n"], 1) if s["quality_n"] > 0 else 0.0
        favs = fav_count.get(pid, 0)
        avg_score = (avg_fit + avg_quality) / 2.0
        heat_score = round(s["tryon_count"] * 0.6 + favs * 0.3 + avg_score * 0.1, 2)
        sort_rows.append({
            "product_id": pid,
            "tryon_count": s["tryon_count"],
            "avg_fit": avg_fit,
            "avg_quality": avg_quality,
            "fav_count": favs,
            "heat_score": heat_score,
        })
    sort_rows.sort(key=lambda x: x["heat_score"], reverse=True)
    for i, row in enumerate(sort_rows, 1):
        row["sort_rank"] = i
    _write_csv(os.path.join(REPORTS_DIR, "ai_product_sort.csv"),
               ["product_id", "tryon_count", "avg_fit", "avg_quality", "fav_count", "heat_score", "sort_rank"],
               sort_rows)

    # ═══════════ 报表2：ai_user_profile.csv ═══════════
    user_rows = []
    for uid, us in user_stats.items():
        total = us["count"]
        sorted_pids = sorted(us["products"], key=lambda p: prod_stats.get(p, {}).get("tryon_count", 0), reverse=True)
        top3 = ",".join(sorted_pids[:3])
        if total >= 20:
            level = "高频用户"
        elif total >= 10:
            level = "中频用户"
        else:
            level = "低频用户"
        user_rows.append({
            "uid": uid,
            "total_tryon": total,
            "top3_product": top3,
            "user_level": level,
        })
    user_rows.sort(key=lambda x: x["total_tryon"], reverse=True)
    _write_csv(os.path.join(REPORTS_DIR, "ai_user_profile.csv"),
               ["uid", "total_tryon", "top3_product", "user_level"],
               user_rows)

    # ═══════════ 报表3：ai_bad_nails.csv ═══════════
    bad_rows = []
    for pid, s in prod_stats.items():
        avg_fit = round(s["fit_sum"] / s["fit_n"], 1) if s["fit_n"] > 0 else 0.0
        avg_quality = round(s["quality_sum"] / s["quality_n"], 1) if s["quality_n"] > 0 else 0.0
        if avg_quality >= 60 and avg_fit >= 55:
            continue
        reasons = []
        if avg_quality < 60:
            reasons.append("质量不达标")
        if avg_fit < 55:
            reasons.append("贴合效果差")
        bad_reason = "双重问题" if len(reasons) >= 2 else reasons[0]
        if bad_reason == "质量不达标":
            suggest = "重新抠图"
        elif bad_reason == "贴合效果差":
            suggest = "调整四点参数"
        else:
            suggest = "综合优化"
        bad_rows.append({
            "product_id": pid,
            "avg_quality": avg_quality,
            "avg_fit": avg_fit,
            "bad_reason": bad_reason,
            "suggest": suggest,
        })
    bad_rows.sort(key=lambda x: x["avg_quality"])
    _write_csv(os.path.join(REPORTS_DIR, "ai_bad_nails.csv"),
               ["product_id", "avg_quality", "avg_fit", "bad_reason", "suggest"],
               bad_rows)

    # ═══════════ 报表4：ai_optimize_suggest.csv ═══════════
    opt_rows = []
    for pid, s in prod_stats.items():
        avg_fit = round(s["fit_sum"] / s["fit_n"], 1) if s["fit_n"] > 0 else 0.0
        avg_quality = round(s["quality_sum"] / s["quality_n"], 1) if s["quality_n"] > 0 else 0.0
        if avg_fit >= 80 and avg_quality >= 80:
            continue
        if avg_quality < 60 and avg_fit < 55:
            s_type = "综合优化"
            detail = f"quality={avg_quality}/fit={avg_fit} 均偏低，建议重新采集甲片并调整四点标注"
        elif avg_quality < 60:
            s_type = "抠图优化"
            detail = f"quality={avg_quality} 偏低，建议使用高分辨率原图重新跑U2Net+SAM抠图流程"
        elif avg_fit < 55:
            s_type = "四点贴合优化"
            detail = f"fit={avg_fit} 偏低，建议检查 nail_points.csv 四点标注位置，参考手部照片修正"
        else:
            s_type = "综合优化"
            detail = f"quality={avg_quality}/fit={avg_fit} 有提升空间，建议微调四点参数并检查甲片边缘"
        opt_rows.append({
            "product_id": pid,
            "current_avg_fit": avg_fit,
            "current_avg_quality": avg_quality,
            "suggest_type": s_type,
            "suggest_detail": detail,
        })
    opt_rows.sort(key=lambda x: min(x["current_avg_fit"], x["current_avg_quality"]))
    _write_csv(os.path.join(REPORTS_DIR, "ai_optimize_suggest.csv"),
               ["product_id", "current_avg_fit", "current_avg_quality", "suggest_type", "suggest_detail"],
               opt_rows)

    print(f"[ai_analyzer] 分析完成，4份报表已写入 {REPORTS_DIR}")


def _write_empty_reports():
    _write_csv(os.path.join(REPORTS_DIR, "ai_product_sort.csv"),
               ["product_id", "tryon_count", "avg_fit", "avg_quality", "fav_count", "heat_score", "sort_rank"], [])
    _write_csv(os.path.join(REPORTS_DIR, "ai_user_profile.csv"),
               ["uid", "total_tryon", "top3_product", "user_level"], [])
    _write_csv(os.path.join(REPORTS_DIR, "ai_bad_nails.csv"),
               ["product_id", "avg_quality", "avg_fit", "bad_reason", "suggest"], [])
    _write_csv(os.path.join(REPORTS_DIR, "ai_optimize_suggest.csv"),
               ["product_id", "current_avg_fit", "current_avg_quality", "suggest_type", "suggest_detail"], [])


if __name__ == "__main__":
    generate_all()
