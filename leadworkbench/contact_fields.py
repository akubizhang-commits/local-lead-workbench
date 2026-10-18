"""Readable contact facts derived from retained, person-scoped evidence.

No new observation is created by this projection. Each row keeps the original
source and time, so reformatting existing records does not claim a fresh crawl.
"""
import re
from urllib.parse import urlsplit
from .contact_quality import invalid_email_reason

ROLE_TRANSLATIONS = {
    "sales manager": "销售经理", "sales representative": "销售代表",
    "sales rep": "销售代表", "buyer": "采购员",
    "purchasing manager": "采购经理", "procurement manager": "采购经理",
    "owner": "负责人", "president": "总裁", "ceo": "首席执行官",
}


def _host(url):
    try:
        return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _source(url, website):
    host = _host(url)
    for domain, label in (("linkedin.com", "LinkedIn"), ("facebook.com", "Facebook"),
                          ("instagram.com", "Instagram"), ("x.com", "X"),
                          ("twitter.com", "X"), ("tiktok.com", "TikTok")):
        if host == domain or host.endswith("." + domain):
            return "社媒 · " + label
    if host and host == _host(website):
        return "企业官网"
    return "其他来源" if host else "来源未记录"


def contact_fields(contact, website=""):
    source_url = contact.get("source_url") or ""
    manual = contact.get("origin") == "manual"
    provenance = dict(source_url=source_url, source_label="人工录入" if manual else _source(source_url, website),
                      observed_at=contact.get("observed_at") or "")
    values = {}
    if manual and contact.get("phone"):
        values["phone"] = contact["phone"]
    if contact.get("email") and not invalid_email_reason(contact["email"]):
        values["email"] = contact["email"]
    if contact.get("role"):
        role = contact["role"]
        translated = ROLE_TRANSLATIONS.get(role.lower())
        values["role"] = f"{role}（{translated}）" if translated else role
    # Never attribute a site's general telephone/language to an unnamed mailbox.
    if contact.get("name") and contact.get("association_status") == "same_block":
        text = contact.get("evidence") or ""
        phone_pattern = r"(Phone|Telephone|Tel|Cell(?:\s*/\s*WhatsApp)?|WhatsApp)\s*:\s*(\+?[\d(][\d() .-]{5,30}\d)"
        for match in re.finditer(phone_pattern, text, re.I):
            raw = match[2].strip()
            digits = re.sub(r"\D", "", raw)
            if not 7 <= len(digits) <= 15:
                continue
            value = ("+" if raw.startswith("+") else "") + digits
            label = match[1].lower()
            if label != "whatsapp":
                values.setdefault("phone", value)
            if "whatsapp" in label:
                values.setdefault("whatsapp", value)
        extension = re.search(r"\b(?:Extension|Ext\.?)\s*:\s*(\d{1,8})\b", text, re.I)
        if extension:
            values["extension"] = extension[1]
        language = re.search(
            r"\bLanguages?\s*:\s*([^|\n]+?)(?=\s+(?:Extension|Ext\.?|Cell|Phone|Email|WhatsApp)\s*:)",
            text, re.I) or re.search(
            r"\bLanguages?\s*:\s*(.+?)(?=\s+(?:Extension|Ext\.?|Cell|Phone|Email|WhatsApp)\s*:|\s*[|\n]|$)",
            text, re.I)
        if language and len(language[1]) <= 80:
            values["language"] = language[1].strip()
    if contact.get("url"):
        values["url"] = contact["url"]
    labels = dict(email="公开邮箱", phone="电话", extension="分机", role="岗位身份",
                  whatsapp="WhatsApp", language="Language", url="联系页面")
    return [dict(key=key, label=label, value=values[key], **provenance)
            for key, label in labels.items() if values.get(key)]
