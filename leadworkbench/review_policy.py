"""Pure review-noise rules shared by both Store write paths and the review cleanup (design §4.2).

No database access here; every function is unit tested in tests/test_review_policy.py.
"""
import json
import re
import unicodedata

from .company_comparison import equivalent_format, phone_format_key

_EXTENSION = re.compile(r'([+\d\s().\-]+?)(?:\s*(?:ext\.?|extension|x|分机|;ext=)\s*(\d+))?', re.I)


def phone_key(value, country):
    """(digits, ext) of the E.164 number without its leading '+', or None.

    Only US (NANP) and ZA have local rules; other countries need an explicit
    international prefix."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"^tel:", "", text, flags=re.I).strip()
    match = _EXTENSION.fullmatch(text)
    if not match:
        return None
    body, ext = match[1].strip(), match[2] or ""
    if "+" in body[1:]:
        return None
    digits = re.sub(r"\D", "", body)
    if body.startswith("+") or body.startswith("00"):
        if body.startswith("00"):
            digits = digits[2:]
        return (digits, ext) if 8 <= len(digits) <= 15 else None
    code = str(country or "").strip().upper()
    if code == "US":
        if len(digits) == 10:
            return "1" + digits, ext
        if len(digits) == 11 and digits.startswith("1"):
            return digits, ext
    elif code == "ZA":
        if len(digits) == 10 and digits.startswith("0"):
            return "27" + digits[1:], ext
        if len(digits) == 9 and not digits.startswith("0"):
            return "27" + digits, ext
        if len(digits) == 11 and digits.startswith("27"):
            return digits, ext
    return None


def phone_compare_key(value, country):
    """E.164 key when it can be derived, otherwise the digits-only key."""
    return phone_key(value, country) or phone_format_key(value)


def phones_equivalent(a, b, country):
    """Old digits-only rule OR the E.164 rule: never stricter than before."""
    if equivalent_format("phone", a, b):
        return True
    left, right = phone_compare_key(a, country), phone_compare_key(b, country)
    return left is not None and left == right


def equivalent(field, old, new, country=None):
    """Whether an observed value only differs from the stored one in format."""
    if field == "phone":
        return phones_equivalent(old, new, country)
    if field == "business_types":
        return enum_business_types(old) == enum_business_types(new)
    return equivalent_format(field, old, new)


# ---- names (design §4.2) ---------------------------------------------------------

GENERIC_SEGMENTS = frozenset({
    "home", "homepage", "home page", "welcome", "shop", "online shop", "store", "official site", "official website",
    "contact us", "about us", "products", "首页", "主页", "欢迎", "官网", "官方网站", "在线商城"})
SEPARATOR = re.compile(r"\s+[|｜–—·:]\s+|\s+-\s+|\s*\|\s*")
TRAILING = ".,;:!?。，；：！？、"
NAME_STOPWORDS = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "co", "company", "corp", "corporation", "pty", "plc", "gmbh",
    "cc", "the", "a", "an", "and", "of"})


def clean_site_name(text):
    """Company name from a page title / site name, or None for slogans, navigation and lists."""
    if text is None:
        return None
    value = " ".join(unicodedata.normalize("NFKC", str(text)).split())
    value = re.sub(r"^(?:welcome to\s+|欢迎来到\s*)", "", value, flags=re.I)
    segments = []
    for segment in SEPARATOR.split(value):
        segment = segment.strip().rstrip(TRAILING).strip()
        if segment and segment.casefold() not in GENERIC_SEGMENTS:
            segments.append(segment)
    if not segments:
        return None
    first = segments[0]
    if len(first) > 80 or len(first.split()) > 10 or first.count(",") > 2:
        return None
    return first


def name_key(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("&", " and ")
    text = re.sub(r"[.'’]", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return tuple(word for word in text.split() if word not in NAME_STOPWORDS)


def name_verdict(current, proposed):
    """equivalent (same words), unrelated (no common word: a slogan) or different (review it)."""
    left, right = name_key(current), name_key(proposed)
    if not left or not right:
        return "different"
    if left == right:
        return "equivalent"
    if not set(left) & set(right):
        return "unrelated"
    return "different"


def website_name_verdict(current, raw):
    """The one wrapper used by the Store and by the cleanup: the proposal is cleaned first."""
    return name_verdict(current, clean_site_name(raw) or raw)


# ---- business types (design §4.2, D5) ---------------------------------------------

BUSINESS_TYPE_ENUM = ("wholesaler", "retailer", "importer")
BUSINESS_TYPE_ALIASES = {"wholesale": "wholesaler", "retail": "retailer", "import": "importer"}
NON_DESCRIPTIVE_PATH = re.compile(
    r"(?:^|/)(?:careers?|jobs?|employment|hiring|join-us|blog|news|posts?|articles?|privacy|terms|policy)(?:/|$)", re.I)


def parse_types(value):
    """A stored or observed business_types value as a list; unreadable values count as empty."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError:
            return []
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def enum_business_types(values):
    """Only the three enum values, mapped from aliases, de-duplicated and sorted."""
    result = set()
    for value in parse_types(values):
        key = " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())
        key = BUSINESS_TYPE_ALIASES.get(key, key)
        if key in BUSINESS_TYPE_ENUM:
            result.add(key)
    return sorted(result)


def business_types_add_nothing(old, new):
    """True when the observation adds no enum value (including none at all)."""
    return set(enum_business_types(new)) <= set(enum_business_types(old))


def is_blank(field, value):
    if value is None or str(value).strip() in ("", "[]"):
        return True
    return field == "business_types" and not parse_types(value)


# ---- change history (design §4.5) -------------------------------------------------

AUTO_FILL_REASON = "自动补全：原值为空"
MANUAL_REASONS = frozenset({"人工修正", "人工接受来源变更", "人工客户匹配判断", "人工联系人采用决定"})


def is_auto_fill(old_value, reason):
    """A change that filled an empty value without a user decision (old rows included, no migration)."""
    return (old_value is None or str(old_value).strip() in ("", "[]")) and reason not in MANUAL_REASONS
