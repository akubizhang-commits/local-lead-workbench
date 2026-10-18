"""Bounded, direct-public website enrichment. Never writes to the business store.

Only static HTML is read. Contact associations are evidence candidates, not
verified employment or deliverability. Limits include robots and redirect work
through a shared wall-clock deadline; ``pages_checked`` counts parsed HTML pages.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup, Tag

from leadworkbench.social import organization_social
from leadworkbench.website_link_policy import contact_page, link_kind
from leadworkbench.contact_quality import (
    GENERAL_LOCAL, DEPARTMENT_LOCAL, has_supplier_intent, is_department,
    is_job_title, is_person_name, is_procurement_role, invalid_contact_reason,
)

USER_AGENT = "LocalLeadWorkbench/0.1"
ROBOTS_AGENT = "LocalLeadWorkbench"
MAX_BODY_BYTES = 1_000_000
MAX_ROBOTS_BYTES = 256_000
WEBSITE_BATCH_SIZE = 20
MAX_DEPTH = 2
MAX_REDIRECTS = 5
MAX_REQUESTS = 60  # Includes robots and every redirect hop, per batch.
MAX_QUEUE = 200
MAX_DOM_NODES = 12_000
MAX_DOM_DEPTH = 64
REQUEST_TIMEOUT = 20
TOTAL_TIMEOUT = 120
MIN_REQUEST_INTERVAL = 2.0
HOST_COOLDOWN = 600
CLOCK_SKEW_TOLERANCE = 1.0
SOCIAL_HOSTS = frozenset({
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "youtube.com", "youtu.be", "tiktok.com", "pinterest.com",
})
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
                   r"@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63})(?![\w.-])")
CARD_CLASS = re.compile(r"(?:^|[-_\s])(person|member|staff|employee|contact-card|team-card)(?:$|[-_\s])", re.I)
GENERIC_HEADING = re.compile(
    r"^(contact(?:\s+us)?|about(?:\s+us)?|(?:our\s+)?team|staff|home|"
    r"suppliers?|products?|welcome|联系我们|关于我们|首页)[.! ]*$", re.I)
HISTORICAL = re.compile(r"\b(former|previous|retired|ex[- ]|past)\b|前任|曾任|离职|離職|退休", re.I)
PRIORITY = re.compile(r"contact|about|team|staff|buyer|supplier|vendor|procurement|purchas|sourcing|联系|关于|采购", re.I)
# Match path segments and explicit router parameters, not arbitrary query text.
# In particular, wholesale/vendor information pages remain eligible.
EXCLUDED_PATH_SEGMENTS = frozenset({
    "product", "products", "collection", "collections", "cart", "checkout", "checkouts",
    "login", "logout", "signin", "sign-in", "account", "accounts",
    "customer_authentication", "customer-authentication", "policies",
    "shipping", "shipping-policy", "shipping-information", "shipping-info",
    "shipping-and-returns", "shipping-returns", "delivery", "delivery-policy",
    "returns", "return-policy", "returns-policy", "refunds", "refund-policy",
    "privacy", "privacy-policy", "terms", "terms-of-service", "terms-and-conditions",
    "search", "register", "registration", "signup", "sign-up",
})


class FetchError(Exception):
    """A bounded retrieval failed."""


class UnsafeURL(FetchError):
    """A URL or resolved connection target is outside the public-web boundary."""


class HTTPError(FetchError):
    def __init__(self, status: int, *, url: str = "", retry_after: str | None = None):
        self.status = status
        self.url = url
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}")


class RobotsDenied(FetchError):
    pass


class PageChallenge(FetchError):
    """An HTTP-success response is an access challenge, not company content."""

    def __init__(self, url: str):
        self.url = url
        self.status = 200
        super().__init__("Website challenge requires manual review")


class WebsiteAccessBlocked(FetchError):
    def __init__(self, access):
        self.access = dict(access)
        super().__init__(self.access["reason"])


class RequestLimit(FetchError):
    pass


class UnparsedDocument(FetchError):
    """A document is not an HTML page and remains available for manual reading."""


class AlreadyFetched(FetchError):
    """A redirect resolved to an already parsed page; no second request needed."""


class ExcludedPage(FetchError):
    """An intentional crawl-policy skip, not a source failure."""


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or any((
        address.is_multicast, address.is_reserved, address.is_unspecified,
        address.is_loopback, address.is_link_local,
    )):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Reject transition/translation mechanisms, including embedded private IPv4.
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network("64:ff9b:1::/48"):
            return False
        if address.scope_id:
            return False
    return True


def normalize_url(url: str) -> str:
    """Normalize public HTTP(S) syntax; DNS names are checked at connection time."""
    if not isinstance(url, str) or not url or len(url) > 4096:
        raise UnsafeURL("Invalid or oversized URL")
    if re.search(r"[\x00-\x20\x7f\\]", url):
        raise UnsafeURL("Whitespace, control characters or backslashes in URL")
    if "://" not in url:
        if url.startswith("//"):
            url = "https:" + url
        elif re.match(r"^[\w.-]+(?::\d+)?(?:[/?#]|$)", url):
            url = "https://" + url
        else:
            raise UnsafeURL("Only HTTP(S) URLs are supported")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise UnsafeURL("Only HTTP(S) URLs are supported")
        if parts.username is not None or parts.password is not None:
            raise UnsafeURL("Credentials in URLs are not allowed")
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port
        expected_port = 443 if parts.scheme.lower() == "https" else 80
        if port is not None and port != expected_port:
            raise UnsafeURL("Only default HTTP(S) ports are allowed")
    except (ValueError, UnicodeError) as exc:
        raise UnsafeURL("Malformed URL") from exc
    if "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        raise UnsafeURL("Local hostnames are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or "." not in host or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise UnsafeURL("Invalid public hostname")
    else:
        if not _public_ip(str(address)):
            raise UnsafeURL("Non-public IP address")
        host = f"[{address.compressed}]" if address.version == 6 else str(address)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}])
    # Canonicalize dot segments before robots checks; aiohttp also normalizes
    # these before sending. Preserve ordinary parameters and repeated slashes.
    path_parts = re.sub(r"%2e", ".", parts.path or "/", flags=re.I).split("/")
    normalized_parts = []
    for part in path_parts:
        if part == "..":
            if len(normalized_parts) > 1:
                normalized_parts.pop()
        elif part != ".":
            normalized_parts.append(part)
    if path_parts[-1] in {".", ".."}:
        normalized_parts.append("")
    path = "/".join(normalized_parts) or "/"
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _site(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _excluded_path(url: str) -> bool:
    parts = urlsplit(url)
    segments = unquote(parts.path).casefold().split("/")
    for key, value in parse_qsl(parts.query):
        if key.casefold() in {"route", "controller"}:
            segments.extend(unquote(value).casefold().split("/"))
    return any(segment in EXCLUDED_PATH_SEGMENTS for segment in segments)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _retry_deadline(value: str | None, now: float) -> tuple[float, bool]:
    """Respect a server deadline without sleeping in the website worker lane."""
    default = now + HOST_COOLDOWN
    if not value:
        return default, False
    value = value.strip()
    # Do not turn an unrepresentably long server wait into an early auto retry.
    if len(value) > 128:
        return default, True
    if re.fullmatch(r"[0-9]+", value):
        if len(value.lstrip("0")) > 10:
            return default, True
        return max(default, now + int(value)), False
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return default, False
        return max(default, date.timestamp()), False
    except (ValueError, TypeError, OverflowError):
        return default, False


class PublicResolver(AbstractResolver):
    """Resolve once per connection and give aiohttp only validated numeric IPs.

    aiohttp uses these records directly, preserving the URL hostname for Host and
    TLS/SNI. There is no separate preflight lookup followed by an unpinned lookup.
    """
    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        answers = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM)
        results, seen = [], set()
        for resolved_family, _, proto, _, address in answers:
            numeric = address[0]
            if not _public_ip(numeric) or (numeric, resolved_family) in seen:
                continue
            seen.add((numeric, resolved_family))
            results.append({
                "hostname": host, "host": numeric, "port": port,
                "family": resolved_family, "proto": proto,
                "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
            })
        if not results:
            raise UnsafeURL("DNS returned no public addresses")
        return results

    async def close(self):
        pass


async def fetch_page(session, url: str, *, max_bytes: int = MAX_BODY_BYTES,
                     robots: bool = False, allow_url=None, consume_request=None,
                     before_request=None) -> dict:
    """Fetch a bounded body, validating every hop before issuing its request."""
    current = normalize_url(url)
    site = _site(current)
    visited = set()
    for _ in range(MAX_REDIRECTS + 1):
        current = normalize_url(current)
        if _site(current) != site:
            raise UnsafeURL("Cross-site redirect requires manual identity review")
        if current in visited:
            raise FetchError("Redirect loop")
        visited.add(current)
        if allow_url is not None:
            await allow_url(current)
        if consume_request is not None:
            consume_request()
        if before_request is not None:
            await before_request(current)
        async with session.get(
            current, allow_redirects=False, proxy=None,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            if response.status in {301, 302, 303, 307, 308}:
                target = response.headers.get("Location")
                if not target:
                    raise FetchError("Redirect without Location")
                target = normalize_url(urljoin(current, target))
                if urlsplit(current).scheme == "https" and urlsplit(target).scheme != "https":
                    raise UnsafeURL("HTTPS redirect downgrade is not allowed")
                current = target
                continue
            if response.status != 200:
                raise HTTPError(response.status, url=current,
                                retry_after=response.headers.get("Retry-After"))
            encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
            if encoding not in {"identity", ""}:
                raise FetchError("Compressed response rejected (identity encoding requested)")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            allowed_types = {"text/html", "application/xhtml+xml"}
            if robots:
                allowed_types |= {"text/plain", ""}
            if content_type in {
                "application/pdf", "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            } and not robots:
                raise UnparsedDocument(current)
            if content_type not in allowed_types:
                raise FetchError(f"Unsupported content type: {content_type or 'missing'}")
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > max_bytes or int(length) < 0:
                        raise FetchError("Response body byte limit exceeded")
                except ValueError as exc:
                    raise FetchError("Invalid Content-Length") from exc
            body = bytearray()
            async for chunk in response.content.iter_chunked(16_384):
                if len(body) + len(chunk) > max_bytes:
                    raise FetchError("Response body byte limit exceeded")
                body.extend(chunk)
            try:
                text = body.decode(response.charset or "utf-8", errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            return {"url": current, "text": text}
    raise FetchError("Redirect limit exceeded")


def _text(node: Tag) -> str:
    return " ".join(node.stripped_strings)


def _evidence(node: Tag, focus=()) -> str:
    # Include literal published destinations when anchor text merely says "Email".
    text = _text(node)
    hrefs = [a.get("href", "") for a in node.find_all("a", href=True)][:12]
    if node.name == "a":
        hrefs.insert(0, node.get("href", ""))
    elif node.name == "form" and node.get("action"):
        hrefs.insert(0, "form action=" + str(node["action"]))
    elif node.name == "meta" and node.get("content"):
        hrefs.insert(0, str(node.get("property") or node.get("name")) + "=" + str(node["content"]))
    # Preserve actual source substrings around fields rather than only the
    # beginning of a long card. Never synthesize a field into its own evidence.
    excerpts = []
    for value in focus:
        if value:
            index = text.lower().find(value.lower())
            if index >= 0:
                excerpts.append(text[max(0, index - 50):index + len(value) + 50])
    context = ("Person block (" + str(node.get("itemtype") or " ".join(node.get("class", []))) + "): "
               if _is_card(node) else "")
    return (context + " | ".join(excerpts) + " | " + text[:1200] +
            (" | " + " | ".join(hrefs) if hrefs else ""))[:2400]


def _is_card(node: Tag) -> bool:
    return bool(CARD_CLASS.search(" ".join(node.get("class", []))) or
                str(node.get("itemtype", "")).rstrip("/").endswith("/Person"))


NON_EMPLOYEE_CLASS = re.compile(
    r"(?:^|[-_\s])(service|services|marketing|benefit|feature|blog|news|post|author|"
    r"byline|breadcrumb|navigation)(?:$|[-_\s])", re.I)


def _schema_type(value, kind: str) -> bool:
    values = value if isinstance(value, list) else str(value or "").split()
    return any(v in {kind, "https://schema.org/" + kind, "http://schema.org/" + kind}
               for v in values if isinstance(v, str))


def _non_employee_context(node: Tag) -> bool:
    for parent in [node, *node.parents]:
        if not isinstance(parent, Tag):
            continue
        if (parent.name in {"nav", "footer", "header"}
                or (parent.name == "article" and not _is_card(parent))
                or parent.get("role") == "navigation"
                or NON_EMPLOYEE_CLASS.search(" ".join(parent.get("class", [])))
                or set(str(parent.get("itemprop", "")).split()) & {"author", "creator"}
                or _schema_type(parent.get("itemtype"), "Person")):
            return True
    return False


def _name_nodes(card: Tag) -> list[Tag]:
    nodes = card.select('[itemprop="name"], .name, .person-name, .member-name, h2, h3, h4, h5')
    result = []
    for node in nodes:
        # Count only distinct, plausible short names, not nested copies.
        text = _text(node)
        if (is_person_name(text)
                and not any(p in nodes for p in node.parents if p is not card)):
            result.append(node)
    return result


def _split_shared_person_cards(soup: BeautifulSoup) -> None:
    """Treat explicit, repeated heading/role sections as separate DOM ranges.

    Some sites place multiple employees in one team-card. Only split a marked
    card with peer name headings and one explicit job-role marker per section.
    Plain page headings and ambiguous or incomplete sections remain untouched.
    The temporary wrappers add no text, attributes or source evidence.
    """
    for card in list(soup.find_all(_is_card)):
        headings = card.find_all(["h2", "h3", "h4", "h5"], recursive=False)
        if not 2 <= len(headings) <= 12 or len(_text(card)) > 3600:
            continue
        if len({h.name for h in headings}) != 1:
            continue
        if [id(n) for n in _name_nodes(card)] != [id(h) for h in headings]:
            continue
        if len({_text(h).casefold() for h in headings}) != len(headings):
            continue
        heading_ids = {id(h) for h in headings}
        sections = []
        for child in list(card.children):
            if id(child) in heading_ids:
                sections.append([])
            if sections:
                sections[-1].append(child)
        for section in sections:
            roles = []
            for part in section:
                if not isinstance(part, Tag):
                    continue
                for node in [part, *part.find_all(True)]:
                    explicit = node.get("itemprop") == "jobTitle" or (
                        set(node.get("class", [])).intersection({"role", "position"}))
                    if explicit:
                        text = _text(node)
                        if is_job_title(text):
                            roles.append(node)
            if len(roles) != 1:
                break
        else:
            for section in sections:
                wrapper = soup.new_tag("div")
                section[0].insert_before(wrapper)
                for part in section:
                    wrapper.append(part.extract())


def _person_card(node: Tag) -> Tag | None:
    if _non_employee_context(node):
        return None
    for ancestor in [node, *list(node.parents)[:10]]:
        if not isinstance(ancestor, Tag) or ancestor.name in {"body", "html", "footer", "nav"}:
            return None
        # Never associate a nested footer with an enclosing person.
        if ancestor.name in {"main", "header"}:
            return None
        if _is_card(ancestor) or ancestor.name in {"article", "li", "tr", "div"}:
            # A generic article/card heading is not an employee record.
            if ancestor.name == "article" and not _is_card(ancestor):
                return None
            names = _name_nodes(ancestor)
            if len(names) > 1:
                return None
            if len(names) == 1 and len(_text(ancestor)) <= 1800:
                # An outer wrapper cannot annex a name from a smaller person
                # block. Both the channel and the name must share that block.
                for parent in names[0].parents:
                    if parent is ancestor:
                        break
                    if isinstance(parent, Tag) and (
                        _is_card(parent) or (
                            parent.name in {"div", "article", "li", "tr"}
                            and len(_name_nodes(parent)) == 1
                        )
                    ):
                        return None
                # An unmarked layout div needs role evidence to be a person card.
                if _is_card(ancestor) or _role(ancestor):
                    return ancestor
    return None


def _role(card: Tag) -> str | None:
    candidates = card.select('[itemprop="jobTitle"], .role, .title, .position, p, span, h4, h5, h6')
    for node in candidates:
        text = _text(node)
        if is_job_title(text) and not _non_employee_context(node):
            if any(_is_card(p) or _name_nodes(p) for p in node.parents
                   if isinstance(p, Tag) and p is not card and card in p.parents):
                continue
            return text
    return None


def _department(node: Tag) -> tuple[Tag, str] | None:
    if _non_employee_context(node):
        return None
    for ancestor in [node, *list(node.parents)[:6]]:
        if not isinstance(ancestor, Tag) or ancestor.name in {"body", "html", "footer", "nav", "main"}:
            return None
        if ancestor.name not in {"section", "div", "article", "li", "td"}:
            continue
        headings = ancestor.find_all(["h2", "h3", "h4", "h5"])
        if (len(headings) == 1 and is_department(_text(headings[0]))
                and not _name_nodes(ancestor) and len(_text(ancestor)) <= 1000):
            for parent in headings[0].parents:
                if parent is ancestor:
                    break
                if isinstance(parent, Tag) and parent.name in {"section", "div", "article", "li", "td"}:
                    return None
            return ancestor, _text(headings[0])[:100]
    return None


MAX_STRUCTURED_BYTES = 128_000
MAX_STRUCTURED_NODES = 512
MAX_STRUCTURED_DEPTH = 16
MAX_STRUCTURED_PEOPLE = 64


def _structured_people(soup: BeautifulSoup, source_url: str, observed_at: str) -> list[dict]:
    """Bounded schema.org records with explicit matching worksFor evidence.

    No remote contexts or references are fetched. Author/creator subtrees and
    referenced authors are excluded even if they also declare an affiliation.
    Microdata properties belong only to their nearest itemscope.
    """
    records, by_id, author_ids = [], {}, set()
    budget = MAX_STRUCTURED_BYTES
    visited = 0
    for script in soup.find_all("script", attrs={"type": re.compile(r"^application/ld\+json$", re.I)})[:16]:
        raw = script.string or script.get_text()
        size = len(raw.encode("utf-8"))
        if size > budget:
            continue
        budget -= size
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        pending = [(payload, 0, False)]
        while pending and visited < MAX_STRUCTURED_NODES:
            node, depth, author = pending.pop()
            visited += 1
            if depth > MAX_STRUCTURED_DEPTH:
                continue
            if isinstance(node, list):
                pending.extend((child, depth + 1, author) for child in reversed(node[:MAX_STRUCTURED_NODES]))
            elif isinstance(node, dict):
                ident = node.get("@id")
                if isinstance(ident, str):
                    ident = urljoin(source_url, ident)
                    if author:
                        author_ids.add(ident)
                    if "@type" in node:
                        by_id[ident] = node
                records.append((node, author))
                for key in ("author", "creator"):
                    refs = node.get(key, [])
                    refs = refs if isinstance(refs, list) else [refs]
                    for ref in refs[:MAX_STRUCTURED_NODES]:
                        if isinstance(ref, str):
                            author_ids.add(urljoin(source_url, ref))
                pending.extend((child, depth + 1, author or key in {"author", "creator"})
                               for key, child in reversed(list(node.items()))
                               if isinstance(child, (dict, list)))

    def same_site(value):
        if not isinstance(value, str) or not value.startswith(("https://", "http://")):
            return False
        try:
            return _site(normalize_url(value)) == _site(source_url)
        except UnsafeURL:
            return False

    company_names = set()
    site_name = soup.find("meta", attrs={"property": "og:site_name"})
    if site_name and isinstance(site_name.get("content"), str):
        company_names.add(site_name["content"].strip().casefold())
    for record, author in records:
        if not author and _schema_type(record.get("@type"), "Organization") and same_site(record.get("url")):
            if isinstance(record.get("name"), str):
                company_names.add(record["name"].strip().casefold())

    def affiliation(value):
        values = value if isinstance(value, list) else [value]
        for organization in values[:16]:
            if isinstance(organization, str):
                organization = {"url": organization} if organization.startswith(("http://", "https://")) else {"name": organization}
            if not isinstance(organization, dict):
                continue
            ident = organization.get("@id")
            resolved = by_id.get(urljoin(source_url, ident)) if isinstance(ident, str) else None
            candidate = resolved or organization
            # A published URL or an absolute organization ID establishes the
            # site. Relative IDs must resolve to a published Organization.
            if candidate.get("url"):
                if same_site(candidate["url"]):
                    return candidate
                continue
            if same_site(candidate.get("@id")):
                return candidate
            name = candidate.get("name")
            if isinstance(name, str) and name.strip().casefold() in company_names:
                return candidate
        return None

    result = []

    def add(record, evidence_kind):
        if len(result) >= MAX_STRUCTURED_PEOPLE:
            return
        name = record.get("name")
        organization = affiliation(record.get("worksFor"))
        if not is_person_name(name) or organization is None:
            return
        raw_role = record.get("jobTitle")
        role = raw_role if is_job_title(raw_role) else None
        email = record.get("email")
        if isinstance(email, str):
            email = re.sub(r"^mailto:", "", email.strip(), flags=re.I)
            if not EMAIL.fullmatch(email) or ".." in email:
                email = None
        else:
            email = None
        shared = email and (GENERAL_LOCAL.fullmatch(email.split("@")[0]) or
                            DEPARTMENT_LOCAL.fullmatch(email.split("@")[0]))
        # Keep the published mailbox as company-only and keep the employee's
        # affiliation independently; never assign a shared channel to a person.
        evidence_record = {k: record[k] for k in ("@type", "name", "jobTitle", "email", "worksFor") if k in record}
        evidence = evidence_kind + ": " + json.dumps(evidence_record, ensure_ascii=False)
        evidence += " | worksFor organization: " + json.dumps(organization, ensure_ascii=False)
        if len(evidence) > 3000:
            return  # Do not emit fields whose published evidence was truncated.
        common = dict(evidence=evidence, source_url=source_url, observed_at=observed_at)
        if shared:
            procurement = bool(DEPARTMENT_LOCAL.fullmatch(email.split("@")[0]))
            result.append(dict(name=None, role=None, email=email, url=None,
                               kind="procurement" if procurement else "general",
                               role_status="department" if procurement else "unknown",
                               association_status="company_only", **common))
            email = None
        if len(result) >= MAX_STRUCTURED_PEOPLE:
            return
        result.append(dict(name=name, role=role, email=email, url=None,
                           kind="procurement" if is_procurement_role(role) else "general",
                           role_status=("historical" if role and HISTORICAL.search(role)
                                        else "explicit" if role else "unknown"),
                           association_status="same_block", **common))

    for record, author in records:
        ident = record.get("@id")
        if (not author and _schema_type(record.get("@type"), "Person")
                and (not isinstance(ident, str) or urljoin(source_url, ident) not in author_ids)):
            add(record, "JSON-LD Person")

    def properties(scope):
        return [node for node in scope.select("[itemprop]")
                if next((p for p in node.parents if p.has_attr("itemscope")), None) is scope]

    def value(node):
        return str(node.get("content") or node.get("href") or _text(node)).strip()

    for scope in soup.select("[itemscope][itemtype]")[:MAX_STRUCTURED_NODES]:
        if not _schema_type(scope.get("itemtype"), "Person"):
            continue
        if (set(str(scope.get("itemprop", "")).split()) & {"author", "creator"}
                or any(_non_employee_context(p) for p in scope.parents if isinstance(p, Tag))):
            continue
        record = {"@type": "Person"}
        for node in properties(scope):
            props = str(node.get("itemprop", "")).split()
            for prop in props:
                if prop in {"name", "jobTitle", "email"}:
                    record[prop] = value(node)
                elif prop == "worksFor":
                    if node.has_attr("itemscope"):
                        organization = {}
                        for child in properties(node):
                            for key in str(child.get("itemprop", "")).split():
                                if key in {"name", "url"}:
                                    organization[key] = value(child)
                        record[prop] = organization
                    else:
                        record[prop] = {"url": node.get("href"), "name": value(node)}
        add(record, "Microdata Person")
    return result


def _contact_form_kind(form: Tag) -> str | None:
    """Require local purpose and an actual message control, not page navigation."""
    def words(value):
        return re.sub(r"[-_/]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value))

    identity = words(" ".join([
        str(form.get(key, "")) for key in ("id", "name", "action", "role", "aria-label", "title")
    ]) + " " + " ".join(form.get("class", [])))
    if re.search(r"\b(?:newsletter|subscribe|subscription|sign\s*up|signup|search|"
                 r"log\s*in|login|sign\s*in|signin|cart|checkout)\b", identity, re.I):
        return None
    # ASP.NET and similar sites can wrap navigation and product content in one
    # form. A Contact Us link in that wrapper does not establish a contact form.
    if form.find(["nav", "main", "form"]) or form.find(attrs={"role": "navigation"}):
        return None
    controls = form.find_all(["input", "textarea"])
    if any(str(control.get("type", "")).lower() in {"password", "search"} for control in controls):
        return None
    message = False
    for control in controls:
        if (control.has_attr("disabled") or control.has_attr("readonly")
                or control.find_parent("fieldset", disabled=True)
                or (control.name == "input" and str(control.get("type", "text")).lower() != "text")):
            continue
        labels = form.find_all("label", attrs={"for": control["id"]}) if control.get("id") else []
        enclosing_label = control.find_parent("label")
        if enclosing_label is not None and form in enclosing_label.parents:
            labels.append(enclosing_label)
        description = words(" ".join(str(control.get(key, "")) for key in (
            "name", "id", "aria-label", "placeholder")) + " " + " ".join(_text(label) for label in labels))
        if re.search(r"\b(?:message|comments?|enquiry|inquiry|question|body|requirements)\b|"
                     r"留言|消息|訊息|咨询|諮詢", description, re.I):
            message = True
            break
    if not message:
        return None
    purpose = []
    for text in form.find_all(string=True):
        for parent in text.parents:
            if parent is form:
                purpose.append(str(text).strip())
                break
            if parent.name in {"a", "nav", "header", "footer", "label", "textarea", "select"}:
                break
    local_text = " ".join(purpose)
    if has_supplier_intent(local_text):
        return "supplier_form"
    if (re.search(r"\bcontact(?:\s*form)?\b", identity, re.I)
            or re.search(r"\b(?:contact\s+(?:us|form|our\s+(?:team|office))|get\s+in\s+touch|"
                         r"(?:send|leave)\s+(?:us\s+)?(?:a\s+)?message|"
                         r"(?:enquiry|inquiry)\s+form)\b|联系我们|聯絡我們|联系表单|留言", local_text, re.I)):
        return "contact_form"
    return None


def parse_page(html: str, source_url: str) -> dict:
    """Conservative HTML rules; no email synthesis or external page execution."""
    result = {"observations": [], "contacts": [], "social_links": [], "links": [], "warnings": []}
    if len(html.encode("utf-8")) > MAX_BODY_BYTES:
        result["warnings"].append("HTML body limit exceeded")
        return result
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.find_all(True, limit=MAX_DOM_NODES + 1)
    if len(nodes) > MAX_DOM_NODES:
        result["warnings"].append("DOM node limit exceeded")
        return result
    for node in nodes:
        for depth, _ in enumerate(node.parents):
            if depth >= MAX_DOM_DEPTH:
                result["warnings"].append("DOM depth limit exceeded")
                return result
    # Require both a challenge-specific title and executable challenge markup.
    # Ordinary captcha articles, escaped code examples and embedded widgets
    # alone are not evidence that access to the company page was challenged.
    title = _text(soup.title) if soup.title else ""
    if re.fullmatch(r"(?:just a moment|checking your browser|verify you are human)[.!…\s]*",
                    title, re.I):
        for script in soup.find_all("script"):
            if ("/cdn-cgi/challenge" in str(script.get("src", "")).casefold()
                    or re.search(r"\b_cf_chl_opt\s*=", script.string or "")):
                raise PageChallenge(source_url)
    had_scripts = bool(soup.find("script"))
    now = datetime.now(timezone.utc).isoformat()
    for node in soup.select("style, noscript, template, [hidden], [aria-hidden='true']"):
        if not node.decomposed:
            node.decompose()
    for node in soup.select("[style]"):
        if not node.decomposed and re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.get("style", ""), re.I):
            node.decompose()
    for social in organization_social(
            [script.string or script.get_text() for script in soup.find_all("script", attrs={"type": re.compile(r"^application/ld\+json$", re.I)})[:16]], source_url):
        if social["url"] not in result["social_links"]:
            result["social_links"].append(social["url"])
            result["observations"].append({"field": "social_links", "value": [social["url"]],
                "source_url": source_url, "evidence": social["evidence"], "observed_at": now})
    structured_people = _structured_people(soup, source_url, now)
    for script in soup.find_all("script"):
        script.decompose()
    _split_shared_person_cards(soup)

    def provenance(node, focus=()):
        return {"source_url": source_url, "evidence": _evidence(node, focus), "observed_at": now}

    def observation(field, value, node):
        result["observations"].append({"field": field, "value": value, **provenance(node)})

    site_name = soup.find("meta", attrs={"property": "og:site_name"})
    if site_name and str(site_name.get("content", "")).strip():
        observation("name", str(site_name["content"]).strip()[:200], site_name)
    elif urlsplit(source_url).path in {"", "/"}:
        # Content-page headings describe a section, not the organization.
        name = soup.find("h1") or soup.find("title")
        if name and _text(name) and not GENERIC_HEADING.fullmatch(_text(name)):
            observation("name", _text(name)[:200], name)
    business_patterns = {
        "wholesaler": r"\bwholesal(?:e|er|ers|ing)\b|批发|批發",
        "distributor": r"\bdistribut(?:or|ors|ion)\b|分销|分銷",
        "manufacturer": r"\bmanufactur(?:er|ers|ing)\b|制造|製造",
        "retailer": r"\bretail(?:er|ers|ing)?\b|零售",
        "importer": r"\bimport(?:er|ers|ing)\b|进口|進口",
    }
    business, snippets = [], []
    for kind, pattern in business_patterns.items():
        match = soup.find(string=re.compile(pattern, re.I))
        if match is not None:
            business.append(kind)
            text = str(match).strip()
            position = re.search(pattern, text, re.I).start()
            snippets.append(text[max(0, position - 100):position + 200])
    if business:
        result["observations"].append({
            "field": "business_types", "value": business, "source_url": source_url,
            "evidence": " | ".join(snippets), "observed_at": now,
        })

    contacts_seen = set()
    cards_with_email = set()

    def add_contact(item):
        if invalid_contact_reason(item):
            return
        key = tuple(str(item.get(k) or "").strip().casefold()
                    for k in ("name", "email", "url", "role", "kind"))
        if key not in contacts_seen:
            contacts_seen.add(key)
            result["contacts"].append(item)

    for item in structured_people:
        add_contact(item)

    def contact(node, email=None, url=None, forced_kind=None):
        card = _person_card(node)
        local = email.split("@", 1)[0] if email else ""
        shared = bool(GENERAL_LOCAL.match(local) or DEPARTMENT_LOCAL.match(local))
        person = _text(_name_nodes(card)[0]) if card is not None and not shared else None
        role = _role(card) if card is not None and not shared else None
        context = card if person else node
        department = _department(node) if not person and not GENERAL_LOCAL.match(local) else None
        if department:
            context, role = department
        text = _text(context)
        procurement = not GENERAL_LOCAL.match(local) and bool(
            is_procurement_role(role) or DEPARTMENT_LOCAL.match(local))
        kind = forced_kind or ("procurement" if procurement else "general")
        if person and email:
            cards_with_email.add(id(card))
        evidence = provenance(context, (person, role, email))
        # The channel may be an attribute of a deeply nested anchor that falls
        # beyond the card's first twelve links.
        if node is not context:
            evidence["evidence"] = (
                _evidence(node, (email,))[:600] + " | " + evidence["evidence"])[:3000]
        add_contact({
            "name": person, "role": role, "email": email, "url": url,
            "kind": kind, "role_status": (
                "department" if department else
                "historical" if role and HISTORICAL.search(text) else
                "explicit" if role else ("department" if procurement else "unknown")),
            "association_status": "same_block" if person else "company_only",
            **evidence,
        })

    def clean_email(raw):
        raw = raw.strip(" \t\r\n.,;:<>")
        if EMAIL.fullmatch(raw) and ".." not in raw and not raw.startswith("."):
            local, domain = raw.rsplit("@", 1)
            if not local.endswith(".") and all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", x)
                for x in domain.split(".")
            ):
                return local + "@" + domain.lower()
        return None

    # Per-node text prevents a distant name/role being joined to a general mailbox.
    for text in soup.find_all(string=EMAIL):
        for match in EMAIL.finditer(str(text)):
            email = clean_email(match.group(1))
            if email:
                contact(text.parent, email=email)
    phones_seen = set()
    for anchor in soup.find_all("a", href=True)[:2000]:
        href = str(anchor["href"]).strip()
        if not href or href == "#":
            continue
        if href.lower().startswith("mailto:"):
            # Extract only explicit recipient addresses; subject/body are not recipients.
            recipients = set()
            for raw in re.split(r"[,;]", unquote(href[7:].split("?", 1)[0])):
                email = clean_email(raw)
                if email:
                    recipients.add(email.casefold())
                    contact(anchor, email=email)
            visible = {email.casefold() for match in EMAIL.finditer(_text(anchor))
                       if (email := clean_email(match.group(1)))}
            if visible and recipients and not visible.issubset(recipients):
                warning = ("Published email conflict: visible " + ", ".join(sorted(visible)[:5]) +
                           "; mailto " + ", ".join(sorted(recipients)[:5]) +
                           ". Both source facts retained; no correction inferred.")
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
            continue
        if href.lower().startswith("tel:"):
            phone = unquote(href[4:]).split(";", 1)[0].strip()
            if 7 <= len(re.sub(r"\D", "", phone)) <= 15 and re.fullmatch(r"[+\d(). \-]+", phone):
                if phone not in phones_seen and _person_card(anchor) is None:
                    observation("phone", phone, anchor)
                    phones_seen.add(phone)
            continue
        try:
            target = normalize_url(urljoin(source_url, href))
        except UnsafeURL:
            continue
        host = urlsplit(target).hostname or ""
        if any(host == known or host.endswith("." + known) for known in SOCIAL_HOSTS):
            if target not in result["social_links"]:
                result["social_links"].append(target)
                observation("social_links", [target], anchor)
        if _site(target) == _site(source_url):
            contexts = [" ".join(node.get("class", []))+" "+str(node.get("id", ""))
                        for node in list(anchor.parents)[:4] if isinstance(node, Tag)]
            kind = link_kind(target, _text(anchor), contexts)
            if kind == "document":
                result.setdefault("files_unparsed", []).append({"url": target, "label": _text(anchor), "source_url": source_url})
                continue
            if kind in {"product", "category", "asset"}:
                result.setdefault("skipped_links", []).append({"url": target, "reason": kind})
                continue
            result["links"].append((target, bool(PRIORITY.search(href + " " + _text(anchor)))))
        # Fetch deduplication strips fragments; the published contact channel
        # keeps a meaningful fragment so a real #contact target remains usable.
        fragment = urlsplit(urljoin(source_url, href)).fragment
        channel_target = target + ("#" + fragment if fragment else "")
        if has_supplier_intent(_text(anchor)):
            contact(anchor, url=channel_target, forced_kind="supplier_form")
        elif re.search(r"contact\s*(us|form)|联系我们|聯絡我們", _text(anchor), re.I):
            contact(anchor, url=channel_target, forced_kind="contact_form")
    form_start = len(result["contacts"])
    for form in soup.find_all("form")[:100]:
        kind = _contact_form_kind(form)
        if kind:
            try:
                target = normalize_url(urljoin(source_url, str(form.get("action") or source_url)))
            except UnsafeURL:
                continue
            contact(form, url=target, forced_kind=kind)
    # Indices of entries produced by <form> elements; enrich_website keeps at
    # most one per site and prefers forms on contact-type pages.
    result["form_contacts"] = list(range(form_start, len(result["contacts"])))
    result["contact_page"] = contact_page(source_url, title)
    # Explicit employee/person blocks remain useful even with both role and
    # email absent. Generic layout headings still require a real job title.
    for node in soup.find_all(["div", "article", "li", "tr", "section"]):
        if id(node) in cards_with_email or _person_card(node) is not node:
            continue
        if len(_name_nodes(node)) == 1 and (_is_card(node) or is_job_title(_role(node))):
            contact(node)
    if had_scripts and len(_text(soup)) < 100:
        result["warnings"].append("Dynamic page may require JavaScript; protected browser rendering is unavailable")
    return result


async def enrich_website(url: str, *, max_pages: int = WEBSITE_BATCH_SIZE,
                         checkpoint: dict | None = None, access_policy=None) -> dict:
    """Return observations and public contacts, with bounded same-site traversal.

    Status: completed = queue exhausted without warnings; partial = useful parsed
    pages plus a limit/failure; blocked = policy/safety/network prevented any page;
    failed = no page and a non-policy HTTP/content failure. A missing robots file
    (404/410) permits crawling; denied, unavailable or malformed robots fails closed.
    ``pages_skipped`` counts unique normalized same-site URLs intentionally
    excluded by path, including redirect targets; these skips do not cause partial.
    Pending contact/about/team/buyer/supplier links precede all ordinary links,
    with discovery order retained within each priority class.
    """
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= WEBSITE_BATCH_SIZE:
        raise ValueError(f"max_pages must be an integer from 1 to {WEBSITE_BATCH_SIZE}")
    result = {"observations": [], "contacts": [], "social_links": [],
              "pages_checked": 0, "pages_skipped": 0, "warnings": [], "status": "completed"}
    try:
        start = normalize_url(url)
    except UnsafeURL as exc:
        result.update(status="blocked", warnings=[str(exc)])
        return result
    # Checkpoints are internal worker state, not accepted from an HTTP caller.
    checkpoint = checkpoint or {}
    if checkpoint and (checkpoint.get("version") != 1 or checkpoint.get("start") != start):
        checkpoint = {}  # A changed website starts a new traversal.
    blocked = False
    skipped = set(checkpoint.get("skipped", []))
    result["warnings"] = [warning for warning in checkpoint.get("warnings", [])
                          if not warning.startswith("Website host waiting: ")]
    result["pages_skipped"] = len(skipped)
    documents = {item["url"]: item for item in checkpoint.get("files_unparsed", [])}
    failures = dict(checkpoint.get("failures", {}))
    # Failures a retry may still turn into a page (429, 5xx, network errors).
    # Only these hold back the fallback form entry; a resume clears failures.
    retryable = {target for target in checkpoint.get("retryable_failures", []) if target in failures}
    failure_warnings = dict(checkpoint.get("failure_warnings", {}))
    logs = list(checkpoint.get("logs", []))
    previous = checkpoint.get("summary", {})
    budget_yield = False
    budget_reason = "Website batch made no progress before its time budget; manual retry required"
    interrupted = None
    interrupted_reason = "batch_budget"
    # Shared policy is authoritative after an explicit host-level resume. Never
    # import another host's gate into this work item's resume/checkpoint scope.
    current_host = _site(start)
    saved_gates = access_policy.gates() if access_policy is not None else checkpoint.get("host_gates", {})
    host_gates = {current_host: saved_gates[current_host]} if current_host in saved_gates else {}
    host_paused = False
    for host, gate in list(host_gates.items()):
        if not gate["manual_required"] and gate["not_before"] <= time.time():
            waiting_warning = f"Website host waiting: {gate['reason']}"
            result["warnings"] = [warning for warning in result["warnings"] if warning != waiting_warning]
            del host_gates[host]

    def skip_page(target):
        skipped.add(target)
        result["pages_skipped"] = len(skipped)

    def warn(message):
        if message not in result["warnings"]:
            result["warnings"].append(message)

    if _excluded_path(start) or link_kind(start) == "asset":
        skip_page(start)
        result.update(status="blocked", warnings=[f"Start URL excluded by crawl path policy: {start}"])
        return result
    resolver = PublicResolver()
    connector = aiohttp.TCPConnector(
        resolver=resolver, use_dns_cache=False, force_close=True, limit=1,
    )
    robots_cache = {}
    requests_made = 0

    def consume_request():
        nonlocal requests_made
        if requests_made >= MAX_REQUESTS:
            raise RequestLimit(f"Total HTTP request limit reached ({MAX_REQUESTS}, including robots and redirects)")
        requests_made += 1

    wire_requests = 0
    last_wire_request = None
    request_origin = None
    request_reservation = None

    class BudgetedRequest(aiohttp.ClientRequest):
        async def send(self, connection):
            nonlocal wire_requests, last_wire_request
            # aiohttp can retry a disconnected GET inside one session.get().
            # Count actual send attempts too, using its supported request class.
            if wire_requests >= MAX_REQUESTS:
                raise RequestLimit(f"Total HTTP request limit reached ({MAX_REQUESTS}, including transport retries)")
            # Connecting can yield long enough for another lane to send. Recheck
            # the shared timing at the wire boundary, reusing only this request's
            # own unchanged reservation. An internal retry always waits again.
            reservation = request_reservation if last_wire_request != requests_made else None
            await pace(request_origin, reservation=reservation)
            last_wire_request = requests_made
            wire_requests += 1
            return await super().send(connection)

    def check_host(target):
        gates = access_policy.gates() if access_policy is not None else host_gates
        gate = gates.get(_site(target))
        if gate and (gate["manual_required"] or gate["not_before"] > time.time()):
            raise WebsiteAccessBlocked(gate)

    def hold_host(gate):
        nonlocal host_paused, blocked
        host_gates[gate["host"]] = gate
        result["website_access"] = gate
        host_paused = blocked = True
        warn(f"Website host waiting: {gate['reason']}")

    def pause_host(target, exc, *, robots=False):
        status = exc.status if isinstance(exc, (HTTPError, PageChallenge)) else None
        manual = status in {401, 403} or isinstance(exc, (aiohttp.ClientSSLError, PageChallenge))
        transient = status in {429, 503} or isinstance(exc, (aiohttp.ClientError, OSError, TimeoutError))
        if not (manual or transient or robots):
            return False
        reason = str(exc).strip() or type(exc).__name__
        if robots:
            reason = "robots unavailable: " + reason
        deadline = 0
        if not manual and (transient or status is not None and status >= 500):
            deadline, manual = _retry_deadline(getattr(exc, "retry_after", None), time.time())
        else:
            manual = True
        host = _site(getattr(exc, "url", "") or target)
        gate = dict(host=host, not_before=deadline, reason=reason,
                    http_status=status, manual_required=manual)
        if access_policy is not None:
            # Persist before returning to extraction/ingestion, which may fail.
            access_policy.observe(gate)
        hold_host(gate)
        return True

    def deny_robots(origin, message, exc=None):
        denial = RobotsDenied(message)
        robots_cache[origin] = denial
        pause_host(origin, exc or denial, robots=True)
        return denial

    last_request = dict(checkpoint.get("last_request", {}))
    request_delays = dict(checkpoint.get("request_delays", {}))

    def record_timing(origin, delay):
        at = time.time()
        last_request[origin] = at
        request_delays[origin] = delay
        if access_policy is not None:
            access_policy.record_request(origin, at, delay)
        return at, delay

    async def pace(origin, *, reservation=None):
        # HTTPS/HTTP and the existing www alias share a conservative host pace;
        # changing an origin must not reset an already observed robots interval.
        while True:
            check_host(origin)
            shared_at, shared_delay = access_policy.timing(origin) if access_policy is not None else (0, 0)
            origins = {origin, *(key for key in last_request if _site(key) == _site(origin))}
            delay = max(MIN_REQUEST_INTERVAL, shared_delay,
                        *(request_delays.get(key, 0) for key in origins))
            previous_request = max(shared_at, *(last_request.get(key, 0) for key in origins))
            now = time.time()
            if previous_request > now + CLOCK_SKEW_TOLERANCE:
                gate = dict(host=_site(origin), not_before=0,
                            reason="System clock moved backwards; check the clock before retrying",
                            http_status=None, manual_required=True)
                if access_policy is not None:
                    access_policy.observe(gate)
                raise WebsiteAccessBlocked(gate)
            if reservation == (previous_request, delay):
                break
            elapsed = max(0, now - previous_request)
            if delay <= elapsed:
                break
            await asyncio.sleep(delay - elapsed)
        # No await between the last shared read and this write: the production
        # lanes share an event loop and see this reservation before they send.
        return record_timing(origin, delay)

    async def before_request(target):
        nonlocal request_origin, request_reservation
        request_origin = _origin(target)
        request_reservation = await pace(request_origin)
    observation_keys = set(checkpoint.get("observation_keys", []))
    contact_keys = set(checkpoint.get("contact_keys", []))
    form_entry = {"kept": False, "fallback": None, **checkpoint.get("form_entry", {})}

    def add_site_contact(item):
        key = repr((item["name"], item["role"], item["email"], item["url"],
                    item["kind"], item["source_url"]))
        if key not in contact_keys:
            contact_keys.add(key)
            result["contacts"].append(item)
    social_seen = set(checkpoint.get("social_seen", []))
    attempted, fetched = set(checkpoint.get("attempted", [])), set(checkpoint.get("fetched", []))
    batch_attempts = 0
    # At most MAX_QUEUE entries: a bounded map permits stable global priority
    # selection and in-place promotion without accumulating stale heap entries.
    queue = {key: tuple(value) for key, value in checkpoint.get("queue", {}).items()} if checkpoint else {start: (0, 0, 0)}
    discovery_order = checkpoint.get("discovery_order", 0)

    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            async with aiohttp.ClientSession(
                connector=connector, trust_env=False, auto_decompress=False,
                request_class=BudgetedRequest,
                cookie_jar=aiohttp.DummyCookieJar(),
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                         "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
            ) as session:
                async def allowed(target):
                    if target in fetched:
                        raise AlreadyFetched(target)
                    if link_kind(target) == "document":
                        raise UnparsedDocument(target)
                    if _excluded_path(target) or link_kind(target) in {"product", "category", "asset"}:
                        skip_page(target)
                        raise ExcludedPage(target)
                    origin = _origin(target)
                    if origin not in robots_cache:
                        try:
                            response = await fetch_page(
                                session, origin + "/robots.txt", robots=True,
                                max_bytes=MAX_ROBOTS_BYTES, consume_request=consume_request,
                                before_request=before_request)
                        except (RequestLimit, WebsiteAccessBlocked):
                            raise
                        except HTTPError as exc:
                            if exc.status not in {404, 410}:
                                raise deny_robots(origin, f"robots unavailable: HTTP {exc.status}", exc) from exc
                            parser = RobotFileParser()
                            parser.parse([])
                        except (FetchError, aiohttp.ClientError, OSError, TimeoutError) as exc:
                            reason = str(exc).strip() or type(exc).__name__
                            raise deny_robots(origin, f"robots unavailable: {reason}", exc) from exc
                        else:
                            text = response["text"]
                            if text.strip() and not re.search(r"(?im)^\s*user-agent\s*:", text):
                                # Plain empty/comment-only files reasonably mean no restrictions.
                                if any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines()):
                                    raise deny_robots(origin, "robots response is not a recognizable robots.txt")
                            parser = RobotFileParser()
                            parser.parse(text.splitlines())
                        robots_cache[origin] = parser
                    parser = robots_cache[origin]
                    if isinstance(parser, RobotsDenied):
                        raise parser
                    if not parser.can_fetch(ROBOTS_AGENT, target):
                        raise RobotsDenied(f"robots disallows {target}")
                    delay = parser.crawl_delay(ROBOTS_AGENT) or 0
                    rate = parser.request_rate(ROBOTS_AGENT)
                    if rate and rate.requests > 0:
                        delay = max(delay, rate.seconds / rate.requests)
                    if delay > REQUEST_TIMEOUT:
                        raise RobotsDenied("robots crawl delay exceeds this run's request budget")
                    request_delays[origin] = max(MIN_REQUEST_INTERVAL, delay)

                while queue and batch_attempts < max_pages:
                    target = min(queue, key=lambda candidate: queue[candidate][:2])
                    try:
                        check_host(target)
                    except WebsiteAccessBlocked as exc:
                        hold_host(exc.access)
                        entry = {"url": target, "status": "deferred", "reason": "host_gate"}
                        if not logs or logs[-1] != entry:
                            logs.append(entry)
                        break
                    position = queue.pop(target)
                    _, _, depth = position
                    if target in attempted or target in fetched:
                        continue
                    if link_kind(target) == "document":
                        documents[target] = {"url": target, "label": "", "source_url": start}
                        continue
                    if _excluded_path(target) or link_kind(target) in {"product", "category", "asset"}:
                        skip_page(target)
                        continue
                    attempted.add(target)
                    batch_attempts += 1
                    interrupted = (target, position)
                    try:
                        response = await fetch_page(
                            session, target, allow_url=allowed, consume_request=consume_request,
                            before_request=before_request)
                        final_url = response["url"]
                        queue.pop(final_url, None)
                        if final_url in fetched:
                            continue
                        # Keep parsing off the event loop so the run deadline and
                        # cancellation remain responsive. No I/O occurs there;
                        # body size and DOM limits bound remaining parser work.
                        parsed = await asyncio.to_thread(parse_page, response["text"], final_url)
                        fetched.add(final_url)
                        interrupted = None
                        logs.append({"url": target, "final_url": final_url, "status": "parsed"})
                    except WebsiteAccessBlocked as exc:
                        hold_host(exc.access)
                        interrupted_reason = "host_gate"
                        break
                    except UnparsedDocument as exc:
                        interrupted = None
                        documents[str(exc)] = {"url": str(exc), "label": "", "source_url": target}
                        logs.append({"url": target, "final_url": str(exc), "status": "file_unparsed"})
                        continue
                    except AlreadyFetched as exc:
                        interrupted = None
                        logs.append({"url": target, "final_url": str(exc), "status": "duplicate"})
                        continue
                    except ExcludedPage as exc:
                        interrupted = None
                        logs.append({"url": target, "status": "excluded", "reason": str(exc)})
                        if target == start:
                            blocked = True
                            warn(f"Start URL redirected to an excluded crawl path: {exc}")
                        continue
                    except RequestLimit as exc:
                        blocked = True
                        budget_reason = str(exc) + "; batch made no progress; manual retry required"
                        # Yield without losing the in-flight target. A zero-progress
                        # batch stops for manual retry instead of spinning forever.
                        budget_yield = True
                        break
                    except (UnsafeURL, RobotsDenied, PageChallenge) as exc:
                        interrupted = None
                        failures[target] = list(position)
                        retryable.discard(target)
                        logs.append({"url": target, "status": "blocked", "reason": str(exc)})
                        blocked = True
                        failure_warnings[target] = f"{target}: {exc}"
                        warn(failure_warnings[target])
                        if isinstance(exc, PageChallenge):
                            pause_host(target, exc)
                        if host_paused:
                            break
                        continue
                    except (FetchError, aiohttp.ClientError, OSError, TimeoutError) as exc:
                        interrupted = None
                        failures[target] = list(position)
                        if (isinstance(exc, (aiohttp.ClientError, OSError, TimeoutError))
                                or isinstance(exc, HTTPError) and (exc.status == 429 or exc.status >= 500)):
                            retryable.add(target)
                        else:
                            retryable.discard(target)
                        reason = str(exc).strip() or type(exc).__name__
                        logs.append({"url": target, "status": "failed", "reason": reason})
                        if (isinstance(exc, (aiohttp.ClientError, OSError, TimeoutError))
                                or isinstance(exc, HTTPError) and exc.status in {401, 403, 429}):
                            blocked = True
                        detail = f": {str(exc).strip()}" if str(exc).strip() else ""
                        failure_warnings[target] = f"{target}: {type(exc).__name__}{detail}"
                        warn(failure_warnings[target])
                        if pause_host(target, exc):
                            blocked = True
                            break
                        continue
                    result["pages_checked"] += 1
                    for item in parsed["observations"]:
                        key = repr((item["field"], repr(item["value"]), item["source_url"]))
                        if key not in observation_keys:
                            observation_keys.add(key)
                            result["observations"].append(item)
                    form_indices = set(parsed.get("form_contacts", ()))
                    for index, item in enumerate(parsed["contacts"]):
                        if index in form_indices:
                            # One form entry per site: the first form on a
                            # contact-type page, else the earliest other form
                            # once the traversal ends (see below).
                            if form_entry["kept"]:
                                continue
                            if not parsed.get("contact_page"):
                                form_entry["fallback"] = form_entry["fallback"] or item
                                continue
                            form_entry.update(kept=True, fallback=None)
                        add_site_contact(item)
                    for link in parsed["social_links"]:
                        if link not in social_seen:
                            social_seen.add(link)
                            result["social_links"].append(link)
                    for document in parsed.get("files_unparsed", []):
                        documents[document["url"]] = document
                    for skipped_link in parsed.get("skipped_links", []):
                        skip_page(skipped_link["url"])
                        logs.append({**skipped_link, "status": "excluded"})
                    for warning in parsed["warnings"]:
                        warn(f"{final_url}: {warning}")
                    for link, priority in sorted(parsed["links"], key=lambda item: not item[1]):
                        if _excluded_path(link):
                            skip_page(link)
                            continue
                        if link in attempted or link in fetched:
                            continue
                        rank = 0 if priority else 1
                        if link in queue:
                            old_rank, order, old_depth = queue[link]
                            queue[link] = (min(rank, old_rank), order, min(depth + 1, old_depth))
                            continue
                        if depth >= MAX_DEPTH:
                            warn("Link depth limit reached")
                            continue
                        if len(queue) >= MAX_QUEUE:
                            warn("Link queue limit reached")
                            worst = max(queue, key=lambda candidate: queue[candidate][:2])
                            if rank >= queue[worst][0]:
                                continue
                            del queue[worst]
                        discovery_order += 1
                        queue[link] = (rank, discovery_order, depth + 1)
                budget_yield = budget_yield or bool(queue)
    except TimeoutError:
        budget_yield = True
        blocked = True
    finally:
        await connector.close()
        await resolver.close()
    if interrupted:
        target, position = interrupted
        attempted.discard(target)
        queue[target] = position
        logs.append({"url": target, "status": "deferred", "reason": interrupted_reason})
    batch_progress = batch_attempts - int(interrupted is not None)
    has_more = bool(queue) and batch_progress > 0 and not host_paused
    if budget_yield and queue and not has_more and not host_paused:
        warn(budget_reason)
    exhausted = not queue and not retryable and not host_paused
    if exhausted and not form_entry["kept"] and form_entry["fallback"]:
        # No contact-type page had a form: keep the earliest form found so the
        # company still shows that it publishes one. A resumable partial stop
        # (retryable page failure, host pause, zero-progress batch) keeps the
        # candidate in the checkpoint, since a retried contact page may still
        # have a form. Permanent failures (404/410, unsafe URL, robots
        # disallow) do not hold it back.
        add_site_contact(form_entry["fallback"])
        form_entry.update(kept=True, fallback=None)
    summary = {"pages_checked": previous.get("pages_checked", 0) + result["pages_checked"],
               "pages_skipped": len(skipped), "contacts_found": len(contact_keys),
               "attempted": len(attempted), "remaining": len(queue),
               "duplicates": sum(log["status"] == "duplicate" for log in logs),
               "failed": len(failures), "files_unparsed": len(documents)}
    if not summary["pages_checked"] and documents:
        blocked = True
        warn("Website entry is a document; no HTML pages parsed")
    if result["warnings"] or queue:
        result["status"] = "partial" if summary["pages_checked"] else ("blocked" if blocked else "partial" if has_more else "failed")
    result.update(has_more=has_more, summary=summary, checkpoint={
        "version": 1, "start": start, "files_unparsed": list(documents.values()), "queue": queue, "attempted": sorted(attempted),
        "fetched": sorted(fetched), "skipped": sorted(skipped), "discovery_order": discovery_order,
        "observation_keys": sorted(observation_keys), "contact_keys": sorted(contact_keys),
        "form_entry": form_entry, "social_seen": sorted(social_seen), "warnings": result["warnings"], "failures": failures,
        "retryable_failures": sorted(retryable), "failure_warnings": failure_warnings, "last_request": last_request, "request_delays": request_delays,
        "host_gates": host_gates,
        "logs": logs, "summary": summary, "budget_warning": budget_reason if budget_yield and queue and not has_more and not host_paused else None,
        "batches": checkpoint.get("batches", 0) + 1})
    return result
