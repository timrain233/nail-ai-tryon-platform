"""
nail_database/log_manager.py - 后端实时运营日志模块
=====================================================
线程安全、追加写入、不覆盖、不丢失

日志1: user_behavior_log.csv  → 用户维度（时间/用户/商品/行为/耗时）
日志2: tryon_debug_log.csv    → 逐指维度（0-4/UNet/四点/渲染/错误）
日志3: heat_operation_report.csv → 商品维度（试戴/收藏/成功率/失败率）
"""
import csv, os, threading
from datetime import datetime
from typing import Optional

NAIL_DATABASE_DIR = os.path.dirname(os.path.abspath(__file__))

BH_LOG = os.path.join(NAIL_DATABASE_DIR, "tryon_behavior_log.csv")
DBG_LOG = os.path.join(NAIL_DATABASE_DIR, "tryon_debug_log.csv")
HEAT_LOG = os.path.join(NAIL_DATABASE_DIR, "tryon_heat_report.csv")

BH_FIELDS = ["time", "user_id", "product_id", "action", "success", "duration_sec", "device_id"]
DBG_FIELDS = ["time", "finger_id", "product_id", "unet_detected", "corners_ok", "render_success", "error_msg"]
HEAT_FIELDS = ["product_id", "product_name", "tryon_count", "tryon_success", "tryon_fail", "favorite_count", "success_rate", "fail_rate"]

_locks = {}


def _lock(path: str) -> threading.Lock:
    if path not in _locks:
        _locks[path] = threading.Lock()
    return _locks[path]


def _ensure_file(path, fields):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()


def _append_csv(path, fields, row):
    _ensure_file(path, fields)
    with _lock(path):
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writerow(row)


def _read_all(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_all(path, fields, rows):
    _ensure_file(path, fields)
    with _lock(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 日志1: 用户行为日志 ──

def log_behavior(user_id: str = "", product_id: str = "",
                 action: str = "", success: bool = True,
                 duration_sec: float = 0.0, device_id: str = ""):
    row = {
        "time": now_str(),
        "user_id": user_id,
        "product_id": product_id,
        "action": action,
        "success": "1" if success else "0",
        "duration_sec": f"{duration_sec:.2f}",
        "device_id": device_id,
    }
    _append_csv(BH_LOG, BH_FIELDS, row)


# ── 日志2: 逐指调试日志 ──

def log_tryon_debug(finger_id: int = -1, product_id: str = "",
                    unet_detected: bool = False, corners_ok: bool = False,
                    render_success: bool = False, error_msg: str = ""):
    row = {
        "time": now_str(),
        "finger_id": str(finger_id),
        "product_id": product_id,
        "unet_detected": "1" if unet_detected else "0",
        "corners_ok": "1" if corners_ok else "0",
        "render_success": "1" if render_success else "0",
        "error_msg": error_msg[:200],
    }
    _append_csv(DBG_LOG, DBG_FIELDS, row)


# ── 日志3: 热度运营报表（实时统计） ──

def update_heat_report(product_id: str, product_name: str = ""):
    """从行为日志重算该商品热度统计"""
    rows = _read_all(BH_LOG)
    prod_rows = [r for r in rows if r.get("product_id", "") == product_id]

    tryon_all = [r for r in prod_rows if r.get("action") == "tryon"]
    tryon_count = len(tryon_all)
    tryon_success = sum(1 for r in tryon_all if r.get("success") == "1")
    tryon_fail = tryon_count - tryon_success
    favorite_count = sum(1 for r in prod_rows if r.get("action") == "favorite")

    success_rate = f"{tryon_success / tryon_count * 100:.1f}%" if tryon_count > 0 else "0.0%"
    fail_rate = f"{tryon_fail / tryon_count * 100:.1f}%" if tryon_count > 0 else "0.0%"

    # 读取已有热度表，更新或追加
    heat_rows = _read_all(HEAT_LOG)
    updated = False
    for hr in heat_rows:
        if hr.get("product_id") == product_id:
            hr.update({
                "product_name": product_name,
                "tryon_count": str(tryon_count),
                "tryon_success": str(tryon_success),
                "tryon_fail": str(tryon_fail),
                "favorite_count": str(favorite_count),
                "success_rate": success_rate,
                "fail_rate": fail_rate,
            })
            updated = True
            break
    if not updated:
        heat_rows.append({
            "product_id": product_id,
            "product_name": product_name,
            "tryon_count": str(tryon_count),
            "tryon_success": str(tryon_success),
            "tryon_fail": str(tryon_fail),
            "favorite_count": str(favorite_count),
            "success_rate": success_rate,
            "fail_rate": fail_rate,
        })

    _write_all(HEAT_LOG, HEAT_FIELDS, heat_rows)


def get_heat_report() -> list[dict]:
    return _read_all(HEAT_LOG)
