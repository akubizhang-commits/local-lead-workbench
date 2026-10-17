"""Presentation-only website groups. Never infer legal identity or merge records."""
from urllib.parse import urlsplit
import re
import unicodedata


def phone_format_key(value):
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    text = re.sub(r'^tel:', '', text, flags=re.I)
    match = re.fullmatch(r'([+\d\s().\-]+?)(?:\s*(?:ext\.?|extension|x|分机|;ext=)\s*(\d+))?', text, re.I)
    if not match:
        return None
    digits = re.sub(r'\D', '', match[1])
    return (digits, match[2] or '') if len(digits) >= 7 else None


def equivalent_format(field, left, right):
    if left == right:
        return True
    if field == 'phone':
        key = phone_format_key(left)
        return key is not None and key == phone_format_key(right)
    # No address, URL-path or company-name inference from visual similarity.
    return False


def website_key(value):
    try:
        url = urlsplit(value or '')
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            return ''
        host = url.hostname.lower().removeprefix('www.')
        port = url.port
        return host + (f':{port}' if port and port not in {80, 443} else '') + url.path.rstrip('/') + ('?' + url.query if url.query else '')
    except ValueError:
        return ''


def brief(row):
    return {key: row[key] for key in ('id', 'name', 'address', 'phone', 'website', 'city', 'country')} if row else None


def branch_identity(value):
    """Same named business on the same site; retain hosted tenant paths."""
    import re
    import unicodedata
    key = website_key(value['website'] or '')
    if not key:
        return ''
    host = key.split('/')[0].split('?')[0]
    # Shared directories cannot establish brand relationships.
    if host in {'facebook.com', 'instagram.com', 'linkedin.com', 'google.com', 'maps.google.com', 'yelp.com', 'linktr.ee', 'sites.google.com'}:
        return ''
    name = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value['name'] or '').casefold()).strip()
    return name + '|' + host if name else ''


def distinct_branches(left, right):
    import re
    def norm(value):
        return re.sub(r'\s+', ' ', value or '').strip().casefold()
    def phone(value):
        return re.sub(r'\D', '', value or '')
    key = branch_identity(left)
    a, b = norm(left['address']), norm(right['address'])
    p, q = phone(left['phone']), phone(right['phone'])
    return bool(key and key == branch_identity(right) and a and b and a != b
                and len(p) >= 7 and len(q) >= 7 and p != q)
