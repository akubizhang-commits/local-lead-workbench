"""Contact entry tiers (design §6.1) and the per-contact facet computation (§1.2).

Pure functions only. ``FACET_VERSION`` must be bumped whenever a rule or word
list here changes; the P0 migrations then recompute every stored facet.
"""
from __future__ import annotations

import json
import re

from .contact_quality import DEPARTMENT_LOCAL, GENERAL_LOCAL, _NON_NAME, is_department

FACET_VERSION = 1

TIERS = (
    ("named_procurement", 1, "具名采购"),
    ("named_role", 2, "具名岗位"),
    ("named_unknown", 3, "具名·岗位未明确"),
    ("department_email", 4, "部门邮箱"),
    ("general_email", 5, "通用邮箱"),
    ("form_phone", 6, "表单 / 联系页面 / 电话"),
    ("company_phone", 7, "仅企业电话"),
    ("none", 8, "无可用入口"),
)
TIER_RANK = {code: rank for code, rank, _ in TIERS}
TIER_LABEL = {code: label for code, _, label in TIERS}
RANK_TIER = {rank: code for code, rank, _ in TIERS}

NAME_LIKE = re.compile(r"^[a-z]{2,}[._-][a-z]{2,}\d{0,2}$")
# Role and department mailbox words: never a person's name (design-review R3-#1).
ROLE_LOCAL_WORDS = _NON_NAME | {
    "accounts", "account", "payable", "receivable", "billing", "desk", "front", "web", "webmaster",
    "master", "online", "care", "customer", "orders", "order", "shop", "store", "wholesale", "trade",
    "export", "import", "dept", "mail", "noreply", "no", "reply", "enquiry", "inquiry", "help"}
# Mailbox words that name a sales/order/service/procurement desk (rank 4, before general rank 5).
DEPARTMENT_LOCAL_WORDS = {
    "sales", "order", "orders", "marketing", "service", "customerservice", "support", "care",
    "purchasing", "procurement", "sourcing", "buyer", "buyers", "supplier", "suppliers"}
DEPARTMENT_PREFIX = re.compile(r"^(sales|orders|marketing|service|customerservice|support)")


def _normalized(value):
    from .market_store import normalized
    return normalized(value)


def email_local(email):
    email = str(email or "").strip()
    return email.rsplit("@", 1)[0].casefold() if "@" in email else ""


def name_like_local(local):
    parts = [p for p in re.split(r"[._-]", local) if p]
    return bool(NAME_LIKE.match(local) and not GENERAL_LOCAL.match(local) and not DEPARTMENT_LOCAL.match(local)
                and not any(p in ROLE_LOCAL_WORDS for p in parts) and not is_department(" ".join(parts)))


def department_local(local):
    parts = [p for p in re.split(r"[._+-]", local) if p]
    return bool(DEPARTMENT_LOCAL.match(local) or DEPARTMENT_PREFIX.match(re.sub(r"[._-]", "", local))
                or any(p in DEPARTMENT_LOCAL_WORDS for p in parts))


def contact_tier(contact):
    """Ordered decision table: the first matching rule wins; rule 6 is the fallback."""
    get = contact.get if hasattr(contact, "get") else (lambda k, d=None: contact[k])
    has_name = bool(_normalized(get("name")))
    email = str(get("email") or "").strip()
    local = email_local(email)
    kind, role_status = get("kind") or "", get("role_status") or ""
    association = get("association_status") or ""
    role = str(get("role") or "").strip()
    if has_name and association == "same_block" and kind == "procurement" and role_status == "explicit":
        code = "named_procurement"
    elif has_name and association == "same_block" and role and role_status == "explicit":
        code = "named_role"
    elif has_name or (local and name_like_local(local)):
        code = "named_unknown"
    elif email and (department_local(local) or (kind == "procurement" and role_status == "department")):
        code = "department_email"
    elif email:
        code = "general_email"
    else:
        code = "form_phone"
    return code, TIER_RANK[code]


def sqlite_email_key(email):
    """Same value as SQLite lower(trim(email)): ASCII-only case folding, spaces only."""
    text = str(email or "").strip(" ")
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def contact_facet(row):
    """(channel_ok, category, tier, tier_rank, identity_key, email_key) for one contact row."""
    from .contact_projection import contact_key, outreach_category
    from .contact_quality import invalid_contact_channel_reason
    row = dict(row)
    channel_ok = not invalid_contact_channel_reason(row.get("kind"), row.get("email"), row.get("url"),
                                                    row.get("evidence"))
    category = outreach_category(row.get("kind"), row.get("role_status"), row.get("email"), row.get("role"))
    tier, rank = contact_tier(row)
    identity = json.dumps(contact_key(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return int(channel_ok), category, tier, rank, identity, sqlite_email_key(row.get("email"))


def company_best_rank(best_contact_rank, phone):
    """Best entry of a company: its best usable contact, else the company phone, else none."""
    if best_contact_rank:
        return int(best_contact_rank)
    return TIER_RANK["company_phone"] if str(phone or "").strip() else TIER_RANK["none"]


def gap_reason(company, best_rank, scan=None):
    """Why the company has no named procurement contact, and what to do next (design §6.2).

    ``scan`` is the latest website refresh work item: {"state": ..., "result": {...}} or None.
    ``company`` may carry ``named_count`` (distinct named usable contacts)."""
    if best_rank == TIER_RANK["named_procurement"] or company.get("status") == "do_not_contact":
        return None
    result = (scan or {}).get("result") or {}
    summary = result.get("summary") or {}
    pages, remaining = summary.get("pages_checked"), summary.get("remaining")
    if not str(company.get("website") or "").strip():
        code, reason, step = "no_website", "没有官网，无法从官网获取联系人", "人工新增联系人，或在社媒中查找"
    elif not company.get("last_checked_at") and scan is None:
        code, reason, step = "not_scanned", "官网尚未扫描", "点击“复核官网资料”"
    elif scan is not None and (scan.get("state") in {"pending", "partial", "blocked", "failed", "running"}
                               or result.get("has_more")):
        code, step = "scan_incomplete", "继续复核官网，或人工新增联系人"
        reason = (f"官网扫描未全部完成（已读 {pages} 页，还剩 {remaining} 个链接）"
                  if pages is not None and remaining is not None else "官网扫描未全部完成")
    elif best_rank >= TIER_RANK["department_email"]:
        code, step = "no_named_people", "人工新增联系人，或补充社媒"
        reason = f"官网已扫描 {pages} 页，未公开具名人员" if pages is not None else "官网已扫描，未公开具名人员"
    else:
        code, step = "no_procurement", "确认现有联系人岗位，或人工新增采购联系人"
        reason = f"已有具名人员 {int(company.get('named_count') or 0)} 位，但未找到采购岗位"
    return {"code": code, "reason": reason, "next_step": step}
