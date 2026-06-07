"""
llm_optimizer.py - 大模型精细化优化引擎
接入 LongCat API（OpenAI 兼容格式），对劣质甲片做缺陷分类与参数建议
异常时自动降级到原有固定逻辑，不中断不崩溃
"""
import os, csv, json, sys, time, traceback
from urllib.request import Request, urlopen
from urllib.error import URLError

_PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _PROJ_ROOT)

CONFIG_PATH = os.path.join(_PROJ_ROOT, "database", "config.json")
BAD_CSV = os.path.join(_PROJ_ROOT, "database", "ai_reports", "ai_bad_nails.csv")
OPT_LOG = os.path.join(_PROJ_ROOT, "database", "auto_optimize_log.csv")
POINTS_CSV = os.path.join(_PROJ_ROOT, "assets", "nail_cut3", "nail_points.csv")
OUT_CSV = os.path.join(_PROJ_ROOT, "database", "llm_fix_suggest.csv")

_OUT_FIELDS = [
    "product_id", "defect_category", "defect_detail",
    "adjust_param", "optimize_times", "risk_tag", "suggest_note"
]


def _load_llm_config():
    default = {
        "enable": True,
        "api_url": "",
        "api_key": "",
        "model": "",
        "timeout": 30,
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


def _write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[llm_optimizer] 输出 {path} ({len(rows)} rows)")
    except Exception as e:
        print(f"[llm_optimizer] 写入失败 {path}: {e}")


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
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
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


def _build_prompt(bad_rows, opt_log_rows, product_id):
    bad_info = None
    for br in bad_rows:
        if (br.get("product_id") or "").strip() == product_id:
            bad_info = br
            break

    if bad_info is None:
        return None

    quality = _safe_float(bad_info.get("avg_quality"))
    fit = _safe_float(bad_info.get("avg_fit"))
    reason = bad_info.get("bad_reason", "")
    suggest = bad_info.get("suggest", "")

    related_logs = [r for r in opt_log_rows
                    if (r.get("product_id") or "").strip() == product_id][-5:]

    log_text = ""
    if related_logs:
        log_text = "历史优化记录：\n" + "\n".join(
            f"  {r.get('time','')[:10]} {r.get('action','')} {r.get('status','')}"
            for r in related_logs
        )

    prompt = f"""你是一个美甲试戴系统的智能优化专家。请分析以下商品数据并输出JSON格式的优化建议。

商品ID: {product_id}
当前quality评分: {quality}/100
当前fit贴合度评分: {fit}/100
已标记问题: {reason}
初步建议: {suggest}

{log_text}

请严格按照以下JSON格式输出（不要包含```markdown标记）：
{{
  "defect_category": "抠图问题 / 贴合问题 / 综合问题",
  "defect_detail": "问题详细描述",
  "adjust_param": {{
    "scale_adjust": "建议缩放比例调整值, 如 +0.02 或 -0.01",
    "rotation_adjust": "建议旋转角度调整值, 如 +2.0 或 -1.5",
    "offset_x": "X方向偏移像素建议",
    "offset_y": "Y方向偏移像素建议"
  }},
  "optimize_times": "建议第几轮优化, 如 1 或 2",
  "risk_tag": "高风险 / 中风险 / 低风险",
  "suggest_note": "具体的优化执行建议"
}}"""
    return prompt


def _try_llm_analyze(product_id, config, bad_rows, opt_log_rows):
    prompt = _build_prompt(bad_rows, opt_log_rows, product_id)
    if prompt is None:
        return None

    messages = [
        {"role": "system", "content": "你是一个专业的美甲试戴系统优化专家，输出严格JSON格式。"},
        {"role": "user", "content": prompt},
    ]

    try:
        content = _call_llm(messages, config)
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            content = content.rsplit("```", 1)[0]
        result = json.loads(content)
        return result
    except Exception as e:
        print(f"[llm_optimizer] LLM 分析商品 {product_id} 失败: {e}")
        return None


def _generate_rule_based(product_id, bad_rows, opt_log_rows):
    bad_info = None
    for br in bad_rows:
        if (br.get("product_id") or "").strip() == product_id:
            bad_info = br
            break
    if bad_info is None:
        return None

    quality = _safe_float(bad_info.get("avg_quality"))
    fit = _safe_float(bad_info.get("avg_fit"))
    reason = bad_info.get("bad_reason", "")
    suggest = bad_info.get("suggest", "")

    if quality < 60 and fit < 55:
        cat = "综合问题"
        detail = f"quality={quality} quality<60 且 fit={fit} fit<55，需重新抠图并调整四点"
        adj = '{"scale_adjust":"+0.03","rotation_adjust":"0","offset_x":"0","offset_y":"0"}'
        risk = "高风险"
        note = suggest or "建议重新采集甲片原图并调整四点标注"
    elif quality < 60:
        cat = "抠图问题"
        detail = f"quality={quality} quality<60，甲片边缘质量不达标"
        adj = '{"scale_adjust":"0","rotation_adjust":"0","offset_x":"0","offset_y":"0"}'
        risk = "中风险"
        note = suggest or "建议使用高分辨率原图重新抠图"
    elif fit < 55:
        cat = "贴合问题"
        detail = f"fit={fit} fit<55，贴合偏差较大"
        adj = '{"scale_adjust":"+0.02","rotation_adjust":"+1.0","offset_x":"0","offset_y":"0"}'
        risk = "中风险"
        note = suggest or "建议微调四点坐标和旋转角度"
    else:
        cat = "综合问题"
        detail = f"quality={quality} fit={fit} 有提升空间"
        adj = '{"scale_adjust":"+0.01","rotation_adjust":"0.5","offset_x":"0","offset_y":"0"}'
        risk = "低风险"
        note = suggest or "轻微调整即可"

    prev_times = sum(1 for r in opt_log_rows
                     if (r.get("product_id") or "").strip() == product_id)
    return {
        "product_id": product_id,
        "defect_category": cat,
        "defect_detail": detail,
        "adjust_param": adj,
        "optimize_times": str(prev_times + 1),
        "risk_tag": risk,
        "suggest_note": note,
    }


def run_llm_optimize():
    print("[llm_optimizer] 开始...")
    config = _load_llm_config()

    if not config.get("enable", True):
        print("[llm_optimizer] LLM 优化开关已关闭，降级到规则模式")
        return _run_rule_based()

    bad_rows = _safe_read_csv(BAD_CSV)
    if not bad_rows:
        print("[llm_optimizer] 无劣质甲片，跳过")
        _write_csv(OUT_CSV, _OUT_FIELDS, [])
        return

    opt_log_rows = _safe_read_csv(OPT_LOG)
    product_ids = sorted(set(
        (br.get("product_id") or "").strip() for br in bad_rows
    ))
    print(f"[llm_optimizer] 待分析商品: {product_ids}")

    results = []
    for pid in product_ids:
        if not pid:
            continue
        print(f"[llm_optimizer]  分析商品 {pid}...")
        llm_result = _try_llm_analyze(pid, config, bad_rows, opt_log_rows)
        if llm_result:
            adj = llm_result.get("adjust_param", {})
            if isinstance(adj, dict):
                adj = json.dumps(adj, ensure_ascii=False)
            results.append({
                "product_id": pid,
                "defect_category": llm_result.get("defect_category", "综合问题"),
                "defect_detail": llm_result.get("defect_detail", ""),
                "adjust_param": str(adj),
                "optimize_times": str(llm_result.get("optimize_times", "1")),
                "risk_tag": llm_result.get("risk_tag", "中风险"),
                "suggest_note": llm_result.get("suggest_note", ""),
            })
            print(f"[llm_optimizer]     LLM 分析完成, risk={llm_result.get('risk_tag','')}")
        else:
            print(f"[llm_optimizer]     LLM 失败，降级规则模式")
            rule_result = _generate_rule_based(pid, bad_rows, opt_log_rows)
            if rule_result:
                results.append(rule_result)

    _write_csv(OUT_CSV, _OUT_FIELDS, results)
    print(f"[llm_optimizer] 完成，输出 {len(results)} 条")


def _run_rule_based():
    bad_rows = _safe_read_csv(BAD_CSV)
    if not bad_rows:
        _write_csv(OUT_CSV, _OUT_FIELDS, [])
        return

    opt_log_rows = _safe_read_csv(OPT_LOG)
    product_ids = sorted(set(
        (br.get("product_id") or "").strip() for br in bad_rows
    ))
    results = []
    for pid in product_ids:
        if not pid:
            continue
        r = _generate_rule_based(pid, bad_rows, opt_log_rows)
        if r:
            results.append(r)
    _write_csv(OUT_CSV, _OUT_FIELDS, results)


if __name__ == "__main__":
    run_llm_optimize()
