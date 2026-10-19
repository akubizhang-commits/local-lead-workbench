"""Pure review-noise rules shared by both Store write paths and the review cleanup (design §4.2).

No database access here; every function is unit tested in tests/test_review_policy.py.
"""
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
    return equivalent_format(field, old, new)
