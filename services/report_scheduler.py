"""
report_scheduler.py - APScheduler 每日定时报表生成 + AI自动优化 + LLM分析 + 报告生成
独立后台进程，不占用业务端口
"""
import sys, os, time, logging

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)

sys.path.insert(0, os.path.join(_PROJ_ROOT, "database"))

LOG_DIR = os.path.join(_PROJ_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "report_scheduler.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("report_scheduler")

REPORT_PID_FILE = os.path.join(_PROJ_ROOT, "report_scheduler.pid")


def run_analysis():
    try:
        from core.ai_analyzer import generate_all
        logger.info("[Scheduler] 开始执行定时数据分析...")
        generate_all()
        logger.info("[Scheduler] 分析完成")
    except Exception as e:
        logger.error("[Scheduler] 分析异常: %s", e, exc_info=True)


def run_optimization():
    try:
        from core.auto_optimizer import run_optimization
        logger.info("[Scheduler] 开始执行自动优化...")
        run_optimization()
        logger.info("[Scheduler] 自动优化完成")
    except Exception as e:
        logger.error("[Scheduler] 自动优化异常: %s", e, exc_info=True)


def run_llm_optimize():
    try:
        from core.llm_optimizer import run_llm_optimize
        logger.info("[Scheduler] 开始执行 LLM 精细优化...")
        run_llm_optimize()
        logger.info("[Scheduler] LLM 优化完成")
    except Exception as e:
        logger.error("[Scheduler] LLM 优化异常: %s", e, exc_info=True)


def run_report_generate():
    try:
        from core.report_generator import run_auto
        logger.info("[Scheduler] 开始生成运营报告...")
        run_auto()
        logger.info("[Scheduler] 报告生成完成")
    except Exception as e:
        logger.error("[Scheduler] 报告生成异常: %s", e, exc_info=True)


def run_full_pipeline():
    run_analysis()
    run_optimization()
    run_llm_optimize()
    run_report_generate()
    try:
        from core.log_rotator import rotate_all
        rotate_all()
        logger.info("[Scheduler] 日志归档完成")
    except Exception:
        pass


def main():
    from core.startup_check import check_all, print_report
    _sr = check_all()
    print_report(_sr, "report_scheduler")

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler()

    scheduler.add_job(run_analysis, CronTrigger(hour=8, minute=0), id="daily_report", name="每日报表")
    scheduler.add_job(run_optimization, CronTrigger(hour=9, minute=0), id="daily_optimize", name="每日自动优化")
    scheduler.add_job(run_llm_optimize, CronTrigger(hour=9, minute=10), id="daily_llm_optimize", name="每日LLM精细优化")
    scheduler.add_job(run_report_generate, CronTrigger(hour=9, minute=20), id="daily_report_gen", name="每日运营报告")

    run_full_pipeline()

    scheduler.start()
    logger.info("[Scheduler] 调度器已启动: 8:00分析 9:00优化 9:10LLM 9:20报告")

    with open(REPORT_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("[Scheduler] 调度器已停止")
        if os.path.exists(REPORT_PID_FILE):
            os.remove(REPORT_PID_FILE)


if __name__ == "__main__":
    main()
