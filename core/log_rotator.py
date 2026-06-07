"""
log_rotator.py - 日志自动切割归档模块
按天归档旧日志，保留指定天数，自动清理过期日志
"""
import os, shutil, time, glob, re, gzip

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_PATH = os.path.join(_PROJ_ROOT, "database", "config.json")


def _load_log_config():
    default = {"max_days": 30, "archive_enabled": True, "archive_dir": "logs/archive"}
    try:
        import json
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            lc = cfg.get("logs", default)
            for k in default:
                lc.setdefault(k, default[k])
            return lc
    except Exception:
        pass
    return default


def rotate_log_file(log_path):
    """对单个日志文件执行按天归档"""
    if not os.path.exists(log_path):
        return

    config = _load_log_config()
    if not config.get("archive_enabled", True):
        return

    archive_dir = os.path.join(_PROJ_ROOT, config.get("archive_dir", "logs/archive").replace("/", os.sep))
    max_days = config.get("max_days", 30)

    os.makedirs(archive_dir, exist_ok=True)

    date_str = time.strftime("%Y%m%d")
    base_name = os.path.basename(log_path)
    name_no_ext = os.path.splitext(base_name)[0]

    archive_name = f"{name_no_ext}.{date_str}.log.gz"
    archive_path = os.path.join(archive_dir, archive_name)

    if os.path.exists(archive_path):
        return

    try:
        with open(log_path, "rb") as f_in:
            with gzip.open(archive_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        open(log_path, "w").close()

        print(f"[log_rotator] 已归档: {base_name} -> {archive_name}")
    except Exception as e:
        print(f"[log_rotator] 归档失败 {base_name}: {e}")

    _cleanup_old(archive_dir, max_days)


def _cleanup_old(archive_dir, max_days):
    cutoff = time.time() - max_days * 86400
    try:
        for f in glob.glob(os.path.join(archive_dir, "*.log.gz")):
            mtime = os.path.getmtime(f)
            if mtime < cutoff:
                os.remove(f)
                print(f"[log_rotator] 已清理过期: {os.path.basename(f)}")
    except Exception:
        pass


def rotate_all():
    log_dir = os.path.join(_PROJ_ROOT, "logs")
    if not os.path.isdir(log_dir):
        return
    for f in glob.glob(os.path.join(log_dir, "*.log")):
        rotate_log_file(f)


if __name__ == "__main__":
    rotate_all()
