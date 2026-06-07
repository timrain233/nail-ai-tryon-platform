"""
cli_monitor.py - NAIL AI 后端命令行运维工具
双击运行，纯文字界面，无前端无网页
"""
import os, sys, json, csv, time, subprocess
from urllib.request import urlopen, Request
from urllib.error import URLError

_PROJ = os.path.abspath(os.path.dirname(__file__))

CONFIG_PATH = os.path.join(_PROJ, "database", "config.json")
OPT_LOG = os.path.join(_PROJ, "database", "auto_optimize_log.csv")
BAD_CSV = os.path.join(_PROJ, "database", "ai_reports", "ai_bad_nails.csv")
RECORDS_CSV = os.path.join(_PROJ, "database", "tryon_records.csv")

SERVICES = [
    ("7860", "首页", "nail_home_server"),
    ("7885", "试戴", "nail_tryon_server"),
    ("7886", "收藏", "nail_fav_page"),
    ("7887", "渲染", "nail_render_server"),
]


def _cls():
    os.system("cls" if os.name == "nt" else "clear")


def _title():
    print("=" * 50)
    print("   NAIL AI - 后端运维监控工具")
    print("=" * 50)
    print()


def _wait():
    input("\n按 Enter 返回菜单...")


def _check_service(port, name, proc_name):
    result = {"port": port, "name": name, "http": "?", "proc": "?"}
    try:
        req = Request(f"http://localhost:{port}/", method="GET")
        with urlopen(req, timeout=3) as r:
            result["http"] = r.status
    except URLError:
        result["http"] = "FAIL"
    except Exception:
        result["http"] = "ERR"
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                f'wmic process where "commandline like \'%{proc_name}%\'" get processid 2>nul',
                shell=True, timeout=3, stderr=subprocess.DEVNULL, text=True
            )
            lines = [l.strip() for l in out.strip().splitlines() if l.strip().isdigit()]
            result["proc"] = len(lines)
        except Exception:
            result["proc"] = "?"
    else:
        try:
            out = subprocess.check_output(
                f"ps aux | grep '{proc_name}' | grep -v grep | wc -l",
                shell=True, timeout=3, stderr=subprocess.DEVNULL, text=True
            )
            result["proc"] = int(out.strip())
        except Exception:
            result["proc"] = "?"
    return result


def print_service_status():
    print(f"{'端口':>6}  {'服务':<8}  {'HTTP':<8}  {'进程':<6}")
    print("-" * 34)
    alive = 0
    for port, name, proc in SERVICES:
        s = _check_service(port, name, proc)
        status = "UP" if str(s["http"]).isdigit() else "DOWN"
        if str(s["http"]).isdigit():
            alive += 1
        http_str = f"{s['http']} {status}"
        proc_str = f"{s['proc']}个" if isinstance(s["proc"], int) else "?"
        print(f"  {port:>4}  {s['name']:<8}  {http_str:<8}  {proc_str:<6}")
    print("-" * 34)
    print(f"  总计: {alive}/{len(SERVICES)} 服务在线")


def menu_service_status():
    _cls()
    _title()
    print("[ 1 ] 服务状态检测\n")
    print_service_status()
    _wait()


def menu_optimize_log():
    _cls()
    _title()
    print("[ 2 ] 最新AI优化日志\n")
    if not os.path.exists(OPT_LOG):
        print("  优化日志文件不存在（尚无优化记录）")
    else:
        try:
            with open(OPT_LOG, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                print("  暂无优化记录")
            else:
                show = rows[-20:]
                print(f"  共 {len(rows)} 条，显示最近 {len(show)} 条:\n")
                fmt = "  {:<20} {:<10} {:<18} {:<12} {:<12} {}"
                print(fmt.format("时间", "商品", "操作", "优化前", "优化后", "状态"))
                print("  " + "-" * 90)
                for r in show:
                    print(fmt.format(
                        (r.get("time") or "")[:19],
                        (r.get("product_id") or ""),
                        (r.get("action") or "")[:16],
                        (r.get("before_val") or "")[:10],
                        (r.get("after_val") or "")[:10],
                        (r.get("status") or ""),
                    ))
        except Exception as e:
            print(f"  读取异常: {e}")
    _wait()


def menu_bad_nails():
    _cls()
    _title()
    print("[ 3 ] 劣质甲片清单\n")
    if not os.path.exists(BAD_CSV):
        print("  报表文件不存在（尚未生成分析报告）")
    else:
        try:
            with open(BAD_CSV, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                print("  无劣质甲片（所有商品质量达标）")
            else:
                print(f"  共 {len(rows)} 个差评商品:\n")
                fmt = "  {:<12} {:<10} {:<10} {:<16} {}"
                print(fmt.format("商品ID", "quality", "fit", "问题类型", "建议操作"))
                print("  " + "-" * 70)
                for r in rows:
                    print(fmt.format(
                        (r.get("product_id") or ""),
                        (r.get("avg_quality") or ""),
                        (r.get("avg_fit") or ""),
                        (r.get("bad_reason") or ""),
                        (r.get("suggest") or ""),
                    ))
        except Exception as e:
            print(f"  读取异常: {e}")
    _wait()


def menu_toggle_optimize():
    _cls()
    _title()
    print("[ 4 ] 开启/关闭 AI 自动优化\n")
    if not os.path.exists(CONFIG_PATH):
        print("  配置文件不存在")
        _wait()
        return
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        current = cfg.get("auto_optimize_enabled", True)
        print(f"  当前状态: {'✅ 开启' if current else '❌ 关闭'}")
        print()
        choice = input("  输入 1 开启 | 0 关闭 | Enter 返回: ").strip()
        if choice == "1":
            cfg["auto_optimize_enabled"] = True
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            print("  → 已开启 AI 自动优化")
        elif choice == "0":
            cfg["auto_optimize_enabled"] = False
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            print("  → 已关闭 AI 自动优化")
        else:
            print("  未变更")
    except Exception as e:
        print(f"  操作失败: {e}")
    _wait()


def menu_system_config():
    _cls()
    _title()
    print("[ 5 ] 系统配置\n")
    if not os.path.exists(CONFIG_PATH):
        print("  配置文件不存在")
    else:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            print("  ════════════════════════════════════")
            print(f"  AI自动优化    : {'开启' if cfg.get('auto_optimize_enabled', True) else '关闭'}")
            print(f"  每轮优化数    : {cfg.get('max_products_per_run', 'N/A')} 个")
            print(f"  四点调整步长  : {cfg.get('point_adjust_step', 'N/A')}")
            print(f"  备份开关      : {'开启' if cfg.get('backup_enabled', True) else '关闭'}")
            print("  ════════════════════════════════════")
            qt = cfg.get("quality_thresholds", {})
            print(f"  fit合格阈值  : ≥{qt.get('fit_score_good', 80)}")
            print(f"  quality合格  : ≥{qt.get('quality_score_good', 80)}")
            print(f"  fit失败阈值  : <{qt.get('fit_score_fail', 55)}")
            print(f"  quality失败  : <{qt.get('quality_score_fail', 60)}")
            print("  ════════════════════════════════════")
            lg = cfg.get("logs", {})
            print(f"  日志保留天数  : {lg.get('max_days', 30)} 天")
            print(f"  日志自动归档  : {'开启' if lg.get('archive_enabled', True) else '关闭'}")
            print("  ════════════════════════════════════")
            sg = cfg.get("segmentor", {})
            print(f"  U2Net最小面积 : {sg.get('u2net_min_area_ratio', 'N/A')}")
            print(f"  U2Net最大面积 : {sg.get('u2net_max_area_ratio', 'N/A')}")
            print(f"  SAM增强开关  : {'开启' if sg.get('sam_enabled', True) else '关闭'}")
            print("  ════════════════════════════════════")
        except Exception as e:
            print(f"  读取异常: {e}")
    try:
        if os.path.exists(RECORDS_CSV):
            with open(RECORDS_CSV, "r", encoding="utf-8") as f:
                rc = len(list(csv.DictReader(f)))
            print(f"  试戴记录总数  : {rc} 条")
    except Exception:
        pass
    _wait()


def main():
    while True:
        _cls()
        _title()
        print("  1. 查看全部服务状态")
        print("  2. 查看最新AI优化日志")
        print("  3. 查看坏甲片清单")
        print("  4. 开启/关闭 AI 自动优化")
        print("  5. 查看系统配置")
        print("  6. 退出")
        print()
        choice = input("  请输入数字 (1-6): ").strip()
        if choice == "1":
            menu_service_status()
        elif choice == "2":
            menu_optimize_log()
        elif choice == "3":
            menu_bad_nails()
        elif choice == "4":
            menu_toggle_optimize()
        elif choice == "5":
            menu_system_config()
        elif choice == "6":
            print("\n  再见。")
            break
        else:
            print("\n  无效输入")
            _wait()


if __name__ == "__main__":
    main()
