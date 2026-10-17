"""Company-level website outcomes for a selected round; no crawling or mutation."""
import json
import re

ENDED = {"succeeded", "partial", "failed", "blocked"}
REASONS = [
    ("access_denied", "网站拒绝访问或要求验证", r"HTTP\s*(401|403)\b|captcha|challenge|access denied|验证页"),
    ("rate_limited", "网站限流或服务暂不可用", r"HTTP\s*(429|5\d\d)\b|rate.limit"),
    ("certificate", "网站证书或安全连接失败", r"certificate|ClientConnectorSSLError|\b(?:SSL|TLS).*(?:error|failed)"),
    ("redirect", "跳转地址无法自动读取", r"cross.site redirect|unsafe.?url|excluded crawl path|public.*address|blocked.*address"),
    ("scan_limit", "达到本次扫描范围或预算", r"time budget|batch_budget|page limit|depth limit|queue limit|request limit|request budget"),
    ("timeout", "网页访问超时", r"timeout|timed out"),
    ("network", "网络或连接失败", r"connect|dns|network|resolve|socket"),
    ("not_found", "网页不存在（404 / 410）", r"HTTP\s*(404|410)\b"),
    ("content_limit", "页面过大或解析受限", r"byte limit|body.*limit|DOM.*limit|compressed.*limit"),
    ("unsupported_content", "网页或附件内容未解析", r"unsupported content type|entry is a document|no HTML pages parsed"),
    ("robots", "网站访问规则或规则文件读取受限", r"\brobots\b"),
    ("skipped", "按企业设置跳过复核", r"已拒绝联系.*跳过"),
]
LABELS = {code: label for code, label, _ in REASONS} | {"other": "其他读取问题（见企业详情）"}


def _object(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _reason(payload, checkpoint):
    access = _object(payload.get("website_access"))
    gates = _object(checkpoint.get("host_gates"))
    gate_signals = []
    for gate in [access, *gates.values()]:
        if isinstance(gate, dict):
            gate_signals += [gate.get("reason", ""), gate.get("reason_code", "")]
            if gate.get("http_status"):
                gate_signals.append(f"HTTP {gate['http_status']}")
    # A source-recorded current access gate takes precedence over historical
    # page warnings, which can span many batches and retries.
    if any(gate_signals):
        return _classify(gate_signals)
    # Log lists span retries: use each URL's latest outcome so recovered pages
    # cannot keep contributing an old denial/timeout to the current summary.
    latest = {}
    logs = checkpoint.get("logs") or []
    for log in logs:
        if isinstance(log, dict) and log.get("url"):
            latest[log["url"]] = log
    recovered = {url for url, log in latest.items() if log.get("status") == "parsed"}
    old_reasons = {log["reason"] for log in logs if isinstance(log, dict)
                   and log.get("url") in recovered and log.get("status") in {"failed", "blocked"}
                   and isinstance(log.get("reason"), str) and log["reason"]}
    failures = _object(checkpoint.get("failure_warnings"))
    signals = [text for url, text in failures.items() if url not in recovered]
    active_failure = any(signals) or any(log.get("status") in {"failed", "blocked"} for log in latest.values())
    signals.extend(log.get("reason", "") for log in latest.values()
                   if log.get("status") in {"failed", "blocked", "deferred"})
    warnings = checkpoint.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if not isinstance(warning, str):
                continue
            if any(warning.startswith(url + ":") for url in recovered):
                continue
            if not active_failure and any(old in warning for old in old_reasons):
                continue
            signals.append(warning)
    # Old workers only saved message. Prefer current structured diagnostics
    # where available, rather than importing obsolete messages from retries.
    if not latest and warnings is None and not any(isinstance(v, str) and v.strip() for v in signals):
        signals.append(payload.get("message", ""))
    return _classify(signals)


def _classify(signals):
    text = " ".join(value for value in signals if isinstance(value, str))
    # Hostnames / query strings are not evidence of an error category.
    text = re.sub(r"https?://\S+", "", text)
    for code, _, pattern in REASONS:
        if re.search(pattern, text, re.I):
            return code
    return "other"


def website_summary(items):
    # Work items already have UNIQUE(run_id,stage,target); guard the projection
    # too, so repeated rows do not turn multiple failed pages into companies.
    unique = {item["target"]: item for item in items}
    result = dict(total=len(unique), processed=0, with_pages=0, without_pages=0,
                  pending=0, running=0, files_unparsed_companies=0, reasons=[])
    groups = {}
    for item in unique.values():
        state = item["state"]
        if state in {"pending", "running"}:
            result[state] += 1
            continue
        if state not in ENDED:
            continue
        result["processed"] += 1
        payload = _object(item.get("result"))
        checkpoint = _object(payload.get("checkpoint"))
        summary = {**_object(checkpoint.get("summary")), **_object(payload.get("summary"))}
        pages = _count(summary.get("pages_checked"))
        if "pages_checked" not in summary:
            pages = len({log.get("final_url") or log.get("url") for log in checkpoint.get("logs", [])
                         if isinstance(log, dict) and log.get("status") == "parsed" and log.get("url")})
        bucket = "with_pages" if pages else "without_pages"
        result[bucket] += 1
        if _count(summary.get("files_unparsed")) or checkpoint.get("files_unparsed"):
            result["files_unparsed_companies"] += 1
        if state == "succeeded":
            continue
        code = _reason(payload, checkpoint)
        group = groups.setdefault(code, dict(code=code, label=LABELS[code],
                                            companies=0, with_pages=0, without_pages=0))
        group["companies"] += 1
        group[bucket] += 1
    result["reasons"] = sorted(groups.values(), key=lambda row: (-row["companies"], row["code"]))
    return result
