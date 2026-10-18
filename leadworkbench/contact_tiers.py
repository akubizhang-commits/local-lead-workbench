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
