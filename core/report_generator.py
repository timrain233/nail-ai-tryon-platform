"""
report_generator.py - 运营日报/周报自动生成
整合全量业务数据，经大模型润色后输出可落地的 Markdown 报告
LLM 不可用时降级为纯数据表格报告
"""
import os, csv, json, sys, time, traceback
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)

CONFIG_PATH = os.path.join(_PROJ_ROOT, "database", "config.json")
REPORT_DIR = os.path.join(_PROJ_ROOT, "report")

RECORDS_CSV = os.path.join(_PROJ_ROOT, "database", "tryon_records.csv")
SORT_CSV = os.path.join(_PROJ_ROOT, "database", "ai_reports", "ai_product_sort.csv")
BAD_CSV = os.path.join(_PROJ_ROOT, "database", "ai_reports", "ai_bad_nails.csv")
OPT_LOG = os.path.join(_PROJ_ROOT, "database", "auto_optimize_log.csv")
HEAT_CSV = os.path.join(_PROJ_ROOT, "nail_database", "tryon_heat_report.csv")


def _load_config():
    default = {
        "enable": True, "api_url": "", "api_key": "", "model": "", "timeout": 30,
    }
    if not os.path.exists(CONFIG_PATH):
        return default
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        llm = cfg.get("llm", default)
        for k in default:
            llm.setdefault(k, default[k])
        return llm
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


def _call_llm(messages, config):
    api_url = (config.get("api_url") or "").rstrip("/")
    api_key = config.get("api_key") or ""
    model = config.get("model") or ""
    timeout = int(config.get("timeout", 30))
    if not api_url or not api_key or not model:
        raise ValueError("LLM 配置不完整")
    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model, "messages": messages,
        "temperature": 0.5, "max_tokens": 4096,
    }).encode("utf-8")
    req = Request(api_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choices = body.get("choices", [])
    if not choices:
        raise ValueError("LLM 返回空 choices")
    return choices[0].get("message", {}).get("content", "")


def _build_data_section():
    lines = []
    sort_rows = _safe_read_csv(SORT_CSV)
    bad_rows = _safe_read_csv(BAD_CSV)
    opt_rows = _safe_read_csv(OPT_LOG)
    heat_rows = _safe_read_csv(HEAT_CSV)

    lines.append("## 一、数据概览\n")
    records = _safe_read_csv(RECORDS_CSV)
    total_tryons = len(records)
    unique_users = len(set((r.get("uid") or "").strip() for r in records if (r.get("uid") or "").strip()))
    unique_products = len(set((r.get("product_id") or "").strip() for r in records if (r.get("product_id") or "").strip()))
    lines.append(f"- 试戴总次数：{total_tryons}")
    lines.append(f"- 试戴用户数：{unique_users}")
    lines.append(f"- 试戴商品数：{unique_products}")
    lines.append(f"- 劣质甲片数：{len(bad_rows)}")
    lines.append(f"- 优化执行次数：{len(opt_rows)}")
    lines.append("")

    if sort_rows:
        lines.append("## 二、商品热度排行（TOP 10）\n")
        lines.append("| 排名 | 商品ID | 试戴数 | 平均fit | 平均quality | 收藏数 | 热度分 |")
        lines.append("|------|--------|--------|----------|-------------|--------|--------|")
        sorted_rows = sorted(sort_rows, key=lambda r: _safe_float(r.get("heat_score")), reverse=True)[:10]
        for i, r in enumerate(sorted_rows, 1):
            lines.append(
                f"| {i} | {r.get('product_id','')} | {r.get('tryon_count','')} | "
                f"{r.get('avg_fit','')} | {r.get('avg_quality','')} | "
                f"{r.get('fav_count','')} | {r.get('heat_score','')} |"
            )
        lines.append("")

    if bad_rows:
        lines.append("## 三、劣质甲片清单\n")
        lines.append("| 商品ID | quality | fit | 问题类型 | 建议 |")
        lines.append("|--------|---------|-----|----------|------|")
        for r in bad_rows:
            lines.append(
                f"| {r.get('product_id','')} | {r.get('avg_quality','')} | "
                f"{r.get('avg_fit','')} | {r.get('bad_reason','')} | {r.get('suggest','')} |"
            )
        lines.append("")

    if opt_rows:
        lines.append("## 四、最近优化记录\n")
        lines.append("| 时间 | 商品 | 操作 | 优化前 | 优化后 | 状态 |")
        lines.append("|------|------|------|--------|--------|------|")
        recent = opt_rows[-10:]
        for r in recent:
            lines.append(
                f"| {(r.get('time') or '')[:19]} | {r.get('product_id','')} | "
                f"{r.get('action','')} | {r.get('before_val','')} | "
                f"{r.get('after_val','')} | {r.get('status','')} |"
            )
        lines.append("")

    if heat_rows:
        hot = sorted(
            [r for r in heat_rows if _safe_float(r.get("tryon_count")) > 0],
            key=lambda r: _safe_float(r.get("tryon_count")), reverse=True
        )[:5]
        if hot:
            lines.append("## 五、最热商品\n")
            for r in hot:
                lines.append(
                    f"- {r.get('product_name','')}：试戴{r.get('tryon_count','')}次 "
                    f"成功率{r.get('tryon_success','')} 收藏{r.get('favorite_count','')}次"
                )
            lines.append("")

    return "\n".join(lines)


def _build_daily_report():
    today = datetime.now().strftime("%Y-%m-%d")
    data_section = _build_data_section()
    config = _load_config()
    llm_enabled = config.get("enable", True) and config.get("api_url") and config.get("api_key")

    if llm_enabled:
        try:
            prompt = f"""你是一个美甲试戴系统的运营分析师。以下是今天({today})的系统运营数据，请生成一份简洁的运营日报。
要求：
1. 以 Markdown 格式输出
2. 包含：今日概况、核心数据、关键问题、运营建议
3. 数据驱动、结论明确、可落地
4. 语气专业、客观

原始数据：
{data_section}"""
            messages = [
                {"role": "system", "content": "你是一个专业的数据分析师，输出 Markdown 格式报告。"},
                {"role": "user", "content": prompt},
            ]
            content = _call_llm(messages, config)
            if "##" in content:
                return f"# NAIL AI 运营日报 ({today})\n\n{content}"
        except Exception as e:
            alert_msg = f"报告异常: LLM 调用失败，已降级为数据表模式: {e}"
            _append_alert_log(alert_msg)

    return f"# NAIL AI 运营日报 ({today})\n\n{data_section}\n---\n*本报告为数据自动汇总（LLM 润色不可用）*"


def _build_weekly_report():
    today = datetime.now().strftime("%Y-%m-%d")
    week_start = datetime.now().strftime("%Y-%m-%d")
    data_section = _build_data_section()
    config = _load_config()
    llm_enabled = config.get("enable", True) and config.get("api_url") and config.get("api_key")

    if llm_enabled:
        try:
            prompt = f"""你是一个美甲试戴系统的资深运营分析师。以下是本周(截至{today})的系统数据，请生成一份全局视角的运营周报。
要求：
1. Markdown 格式
2. 包含：本周总览、爆款分析、质量问题复盘、优化成效、下周执行建议
3. 给出具体的爆款/下滑/滞销判断
4. 给出可落地运营动作建议
5. 语气专业、严谨

原始数据：
{data_section}"""
            messages = [
                {"role": "system", "content": "你是一个资深运营分析师，输出 Markdown 格式周报。"},
                {"role": "user", "content": prompt},
            ]
            content = _call_llm(messages, config)
            if "##" in content:
                return f"# NAIL AI 运营周报 (截至 {today})\n\n{content}"
        except Exception as e:
            _append_alert_log(f"周报异常: LLM 调用失败，已降级: {e}")

    return f"# NAIL AI 运营周报 (截至 {today})\n\n{data_section}\n---\n*本报告为数据自动汇总（LLM 润色不可用）*"


def _append_alert_log(msg):
    try:
        log_path = os.path.join(_PROJ_ROOT, "logs", "report_generator_alert.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _write_report(filename, content):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[report_generator] 已生成: {path}")
    except Exception as e:
        print(f"[report_generator] 写入失败 {path}: {e}")


def run_daily():
    print("[report_generator] 生成日报...")
    content = _build_daily_report()
    date_str = datetime.now().strftime("%Y%m%d")
    _write_report(f"daily_{date_str}.md", content)


def run_weekly():
    print("[report_generator] 生成周报...")
    content = _build_weekly_report()
    date_str = datetime.now().strftime("%Y%m%d")
    _write_report(f"weekly_{date_str}.md", content)


def run_auto():
    now = datetime.now()
    run_daily()
    if now.weekday() == 0:
        run_weekly()


if __name__ == "__main__":
    run_auto()
