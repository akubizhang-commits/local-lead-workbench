"""Chinese messages for request validation errors (HTTP 422).

Output items keep only loc/msg/type so user input (input/ctx) is never echoed."""
import re

# Exact texts raised by sources.website.normalize_url.
URL_MESSAGES = {
    "Non-public IP address": "官网不能是内网、本机或保留 IP 地址",
    "Only default HTTP(S) ports are allowed": "官网只能使用默认端口（80/443）",
    "Only HTTP(S) URLs are supported": "网址必须以 http:// 或 https:// 开头",
    "Credentials in URLs are not allowed": "网址中不能包含用户名或密码",
    "Local hostnames are not allowed": "官网不能是本机或局域网主机名",
    "Invalid public hostname": "官网域名无效",
    "Invalid or oversized URL": "网址无效或过长",
    "Whitespace, control characters or backslashes in URL": "网址中不能包含空格、控制字符或反斜杠",
    "Malformed URL": "网址格式不正确",
}

CHINESE = re.compile(r"[\u4e00-\u9fff]")


def _limit(ctx, *keys):
    for key in keys:
        if isinstance(ctx, dict) and key in ctx:
            return ctx[key]
    return None


def _by_type(kind, ctx):
    if kind == "missing":
        return "此项为必填"
    if kind == "string_too_short":
        size = _limit(ctx, "min_length")
        return f"至少 {size} 个字符" if isinstance(size, int) and size > 1 else "不能为空"
    if kind == "string_too_long":
        return f"不能超过 {_limit(ctx, 'max_length')} 个字符"
    if kind == "too_short":
        return f"至少 {_limit(ctx, 'min_length')} 项"
    if kind == "too_long":
        return f"最多 {_limit(ctx, 'max_length')} 项"
    if kind in {"literal_error", "enum"}:
        return "不是可选的值"
    if kind == "extra_forbidden":
        return "不支持此字段"
    if kind == "json_invalid":
        return "请求内容不是有效的 JSON"
    if kind in {"int_type", "int_parsing"}:
        return "必须是整数"
    if kind in {"greater_than_equal", "greater_than"}:
        return f"不能小于 {_limit(ctx, 'ge', 'gt')}"
    if kind in {"less_than_equal", "less_than"}:
        return f"不能大于 {_limit(ctx, 'le', 'lt')}"
    if kind == "string_type":
        return "必须是文本"
    if kind in {"bool_type", "bool_parsing"}:
        return "必须是“是”或“否”"
    if kind == "list_type":
        return "必须是列表"
    if kind in {"dict_type", "model_type", "model_attributes_type"}:
        return "格式不正确"
    return None


def translate_message(msg, kind="", ctx=None):
    text = str(msg or "").removeprefix("Value error, ").strip()
    if text in URL_MESSAGES:
        return URL_MESSAGES[text]
    if CHINESE.search(text):
        return text
    return _by_type(kind, ctx) or f"输入不符合要求（{text[:120]}）"


def translate_errors(errors):
    return [{"loc": list(error.get("loc") or []),
             "msg": translate_message(error.get("msg"), error.get("type", ""), error.get("ctx")),
             "type": error.get("type", "")} for error in errors]
