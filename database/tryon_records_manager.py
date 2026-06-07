"""
tryon_records_manager.py - 试戴历史记录持久化（含评分）
"""
import csv, os, threading

_db_lock = threading.Lock()

_RECORDS_CSV = None

def _get_csv_path():
    global _RECORDS_CSV
    if _RECORDS_CSV is None:
        _RECORDS_CSV = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "tryon_records.csv"
        )
    return _RECORDS_CSV

_COLUMNS = ["uid", "product_id", "raw_image", "result_image", "create_time", "fit_score", "quality_score"]

def _ensure_header(fpath):
    if not os.path.exists(fpath) or os.path.getsize(fpath) == 0:
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_COLUMNS)

def append_tryon_record(uid, product_id, raw_image, result_image, create_time, fit_score="", quality_score=""):
    if not uid or not product_id:
        return
    fpath = _get_csv_path()
    with _db_lock:
        _ensure_header(fpath)
        try:
            with open(fpath, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([uid, product_id, raw_image, result_image, create_time, fit_score, quality_score])
        except Exception:
            pass

def read_tryon_records(uid, limit=100):
    if not uid:
        return []
    fpath = _get_csv_path()
    if not os.path.exists(fpath):
        return []
    result = []
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                r_uid = (row.get("uid") or "").strip()
                if r_uid != uid:
                    continue
                result.append({
                    "item_id": (row.get("product_id") or "").strip(),
                    "item_name": "",
                    "scene": "",
                    "hand": "",
                    "image": (row.get("result_image") or "").strip(),
                    "raw_image": (row.get("raw_image") or "").strip(),
                    "time": (row.get("create_time") or "").strip(),
                    "fit_score": (row.get("fit_score") or "").strip(),
                    "quality_score": (row.get("quality_score") or "").strip(),
                })
    except Exception:
        pass
    return result
