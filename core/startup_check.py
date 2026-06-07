"""
startup_check.py - 启动资源自检模块
服务启动时检查关键资源是否存在，缺失输出警告不崩溃
"""
import os, sys

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_REQUIRED_DIRS = [
    ("assets/raw_images", "商品原图目录"),
    ("assets/nail_cut3", "甲片抠图目录"),
    ("nail_database", "数据库目录"),
    ("database", "运行时数据库目录"),
]

_REQUIRED_FILES = [
    ("assets/nail_cut3/nail_points.csv", "甲片四点坐标文件"),
]

_OPTIONAL_DIRS = [
    ("checkpoints", "模型权重目录"),
]

_OPTIONAL_FILES = [
    ("assets/raw_images/img_001.webp", "商品图样本(用于验证目录可读)"),
]


def check_all():
    results = {"ok": True, "warnings": [], "errors": []}

    for sub, label in _REQUIRED_DIRS:
        p = os.path.join(_PROJ_ROOT, sub.replace("/", os.sep))
        if os.path.isdir(p):
            files = [f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))]
            results["warnings"].append(f"  [{label}] {sub} ({len(files)} files)")
        else:
            results["ok"] = False
            results["errors"].append(f"  [{label}] {sub} 缺失")

    for sub, label in _REQUIRED_FILES:
        p = os.path.join(_PROJ_ROOT, sub.replace("/", os.sep))
        if os.path.exists(p):
            sz = os.path.getsize(p)
            results["warnings"].append(f"  [{label}] {sub} ({sz} bytes)")
        else:
            results["ok"] = False
            results["errors"].append(f"  [{label}] {sub} 缺失")

    for sub, label in _OPTIONAL_DIRS:
        p = os.path.join(_PROJ_ROOT, sub.replace("/", os.sep))
        if os.path.isdir(p):
            results["warnings"].append(f"  [{label}] {sub} 就绪")
        else:
            results["warnings"].append(f"  [{label}] {sub} 未找到(可选)")

    for sub, label in _OPTIONAL_FILES:
        p = os.path.join(_PROJ_ROOT, sub.replace("/", os.sep))
        if os.path.exists(p):
            results["warnings"].append(f"  [{label}] {sub} 就绪")
        else:
            results["warnings"].append(f"  [{label}] {sub} 不可用(可选)")

    return results


def print_report(results, service_name="未知服务"):
    print(f"[{service_name}] ──── 启动资源自检 ────")
    for w in results["warnings"]:
        print(f"[{service_name}] {w}")
    if results["errors"]:
        for e in results["errors"]:
            print(f"[{service_name}] ⚠ {e}")
        if not results["ok"]:
            print(f"[{service_name}] ⚠ 部分资源缺失，服务仍将启动")
    else:
        print(f"[{service_name}] ✅ 资源检查通过")
    print(f"[{service_name}] ─────────────────────")
