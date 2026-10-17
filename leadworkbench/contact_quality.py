"""Conservative, deterministic contact checks shared by extraction and cleanup.

These helpers validate published field shapes, not identity or employment.
``invalid_contact_reason`` accepts dicts and other key-addressable records (such
as sqlite rows), does no IO, and returns a stable reason code or None. A missing
email alone never invalidates a named employee with an explicit role.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

GENERAL_LOCAL = re.compile(
    r'^(info|contact|hello|office|admin|support|sales|service|customerservice|'
    r'enquiries|inquiries|marketing|orders|team|hr|careers|reception)(?:[+._-].*)?$', re.I)
DEPARTMENT_LOCAL = re.compile(
    r'^(purchasing|procurement|sourcing|buyers?|suppliers?)(?:[+._-].*)?$', re.I)
_PROCUREMENT = re.compile(
    r'\b(buyer|purchasing|procurement|sourcing|supply\s+chain|achats|acheteur|'
    r'compras|comprador|einkauf)\b|采购|採購|購買', re.I)
_NON_NAME = frozenset(('quality control customer service contact about team staff department '
    'registration company welcome clothing apparel services service supplier suppliers vendor '
    'message business hours history requirements location protection solutions solution '
    'production runs plan smaller realistic options why use instead searching alone '
    'our your their with for from get touch send news article blog fashion sourcing '
    'procurement purchasing buyer buyers sales manager director officer president '
    'founder owner ceo head supply chain home products welcome support help '
    'marketing logistics finance human resources general enquiries '
    'resource guidance product development management fabric trim trims sample making').split())
_TITLE_WORDS = frozenset(('buyer purchasing procurement sourcing supply chain director manager '
    'officer president sales founder cofounder co-founder owner ceo cfo coo cto head chief '
    'vice vp svp evp senior junior assistant associate executive lead leader specialist '
    'coordinator analyst consultant supervisor administrator representative rep agent '
    'global regional national international group division divisional category product '
    'production operations commercial merchandising merchandise retail wholesale '
    'apparel clothing textile textiles technologist fashion footwear accessories menswear womenswear '
    'home goods materials raw strategic technical development business quality control '
    'customer service marketing finance financial hr human resources logistics '
    'of and for the achats acheteur compras comprador einkauf responsable '
    'former previous retired ex').split())
_TITLE_CORE = re.compile(
    r'\b(buyer|director|manager|officer|president|founder|cofounder|owner|ceo|cfo|coo|cto|'
    r'head|vp|svp|evp|specialist|coordinator|analyst|consultant|supervisor|administrator|'
    r'representative|rep|agent|lead|leader|technologist|acheteur|comprador|responsable)\b', re.I)
_DEPARTMENT = re.compile(
    r'^(?:(?:global|regional|corporate)\s+)?(?:procurement|purchasing|sourcing|supply chain|'
    r'buying|sales|customer service|support|marketing|human resources|finance|logistics|'
    r'quality control|achats|compras|einkauf)(?:\s+(?:department|team|office|desk))?$'
    r'|^(?:采购|採購|销售|銷售|客服)(?:部|部门|部門|团队|團隊)?$', re.I)
_SUPPLIER = re.compile(r'\b(suppliers?|vendors?)\b|供应商|供應商', re.I)
_SUPPLIER_ACTION = re.compile(
    r'\b(?:supplier|vendor)s?\s+(?:application|registration|onboarding|enrolment|'
    r'enrollment|form|contact|enquiries|inquiries|portal)\b|'
    r'\b(?:apply|register|registration|become|join|contact|submit)\s+'
    r'(?:(?:to|as|a|an|our|new|the|become|potential)\s+){0,4}(?:supplier|vendor)s?\b|'
    r'(?:供应商|供應商).{0,4}(?:申请|申請|注册|註冊|入驻|入駐|联系|聯絡)|'
    r'(?:联系|聯絡|申请|申請|注册|註冊).{0,4}(?:供应商|供應商)', re.I)


def _clean(value) -> str:
    return ' '.join(value.split()) if isinstance(value, str) else ''


def invalid_email_reason(value) -> str | None:
    """Check captured mailbox shape only; never infer delivery or ownership.

    Do not blacklist free mail providers, international domains, plus aliases,
    or a whole domain merely because its site contains a third-party service.
    Empty values remain valid for contacts with other published channels.
    """
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        return 'invalid_email_format'
    email = value.strip()
    if email.count('@') != 1 or len(email) > 254 or re.search(r'[\s\x00-\x1f\x7f<>]', email):
        return 'invalid_email_format'
    local, domain = email.rsplit('@', 1)
    if (not local or len(local) > 64 or local.startswith('.') or local.endswith('.')
            or '..' in local or any(not (c.isalnum() or c in ".!#$%&'*+/=?^_`{|}~-") for c in local)):
        return 'invalid_email_format'
    try:
        domain = domain.encode('idna').decode('ascii').lower()
    except UnicodeError:
        return 'invalid_email_format'
    labels = domain.split('.')
    if (len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                              for label in labels) or labels[-1].isdigit()):
        return 'invalid_email_format'
    # Explicit template values observed in source forms, not arbitrary names.
    if (email.casefold() in {'filler@godaddy.com', 'youremail@here.com'}
            or local.casefold() in {'youremail', 'your.email', 'your-email', 'your_email',
                                    'youremailaddress', 'your-email-address'}):
        return 'placeholder_email'
    return None


def non_outreach_email_reason(value) -> str | None:
    """Published mailboxes whose stated role is unrelated to customer outreach."""
    local = str(value or '').strip().casefold().split('@', 1)[0].split('+', 1)[0]
    compact = re.sub(r'[._-]', '', local)
    if compact in {'noreply', 'donotreply', 'do_not_reply', 'unsubscribe'}:
        return 'non_reply_email'
    if re.search(r'(?:^|[._-])(privacy|abuse|dpo)(?:$|[._-])', local):
        return 'privacy_or_abuse_email'
    # Role mailbox prefixes are evidence; surnames or brand-name suffixes are
    # not. For example steve.jobs and brandjobs must not become HR by substring.
    if re.search(r'^(jobs?|careers?|recruitment|recruiting|hr)(?:$|[._-])', local):
        return 'recruitment_email'
    return None


def invalid_contact_channel_reason(kind, email, url, evidence) -> str | None:
    """Shared read/ingest gate for demonstrably unusable captured channels."""
    if reason := invalid_email_reason(email):
        return reason
    if kind not in {'contact_form', 'supplier_form'}:
        return None
    text = str(evidence or '')
    # Preserve real section links (#contact) but not a menu's bare # placeholder.
    if re.search(r'\|\s*#\s*$', text):
        return 'placeholder_contact_link'
    try:
        parsed = urlsplit(str(url or ''))
        path = parsed.path.casefold()
    except ValueError:
        return 'invalid_contact_link'
    if not parsed.hostname or parsed.scheme not in {'https', 'http'}:
        return 'invalid_contact_link'
    # These destinations have an explicit purpose other than contacting staff.
    if re.search(r'(?:^|/)(?:newsletter(?:[-_]sign[-_]?up)?|subscribe|unsubscribe|'
                 r'cart|checkout|wishlist|productresults|product-category|my-account)(?:/|$)', path):
        return 'non_contact_form'
    if (re.search(r'\bsearch\s+all\s+products\b', text, re.I)
            and not re.search(r'(?:^|/)(?:contact[-_]?us|contact|enquir(?:y|ies))(?:/|$)', path)
            and not re.search(r'\b(?:message|enquiry|inquiry|question)\b|留言|消息|咨询', text, re.I)
            and not has_supplier_intent(text)):
        return 'page_wrapper_form'
    if 'form action=' in text:
        body, destinations = text.split('form action=', 1)
        # ASP.NET-style outer forms capture site navigation on every page.
        # Only reject strong wrapper evidence, not an ordinary short form.
        explicit = re.search(r'contact\s*(?:us|form)|message|enquir|inquir|'
                             r'get in touch|联系|聯絡', body, re.I)
        if (not explicit and body.strip().lower().startswith('| home ')
                and re.search(r'\b(?:privacy policy|login|copyright)\b', body, re.I)
                and destinations.count('|') >= 6):
            return 'page_wrapper_form'
    return None


def is_person_name(value) -> bool:
    """Plausible short published name, including mononyms; never invent a name."""
    text = _clean(value)
    if not 2 <= len(text) <= 80 or any(c.isdigit() for c in text):
        return False
    words = text.split()
    if len(words) > 5:
        return False
    name_words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text).split()
    if any(w.casefold().strip(".'’-") in _NON_NAME for w in name_words):
        return False
    if re.search(r'公司|团队|團隊|部门|部門|采购|採購|服务|服務|质量|質量', text):
        return False
    if any(not (c.isalpha() or unicodedata.category(c).startswith('M') or c in " .'’-" ) for c in text):
        return False
    # Lowercase particles are allowed within names, not arbitrary lowercase prose.
    return all(w in {'de', 'del', 'da', 'di', 'van', 'von', 'der', 'al', 'bin', 'la'} or
               next((c for c in w if c.isalpha()), '').isupper() or
               all(not c.isascii() for c in w if c.isalpha()) for w in words)


def is_job_title(value) -> bool:
    """Recognize a bounded occupational title rather than keyword-bearing prose."""
    text = _clean(value)
    if not text or len(text) > 100:
        return False
    if re.fullmatch(r'(?:前任|曾任)?(?:采购|採購|销售|銷售|供应链|供應鏈)?(?:经理|經理|总监|總監|主管|负责人|負責人|采购员|採購員)', text):
        return True
    words = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", text.lower())
    if not words or len(words) > 12 or re.search(r'[^A-Za-z\s/&(),.\-]', text):
        return False
    extra_titles = {'engineer', 'designer', 'technologist', 'accountant'}
    if any(word not in _TITLE_WORDS | extra_titles for word in words):
        return False
    return bool(_TITLE_CORE.search(text) or set(words) & extra_titles) or text.casefold() in {
        'procurement', 'purchasing', 'sourcing', 'sales', 'achats', 'compras', 'einkauf'}


def is_department(value) -> bool:
    return bool(_DEPARTMENT.fullmatch(_clean(value)))


def is_procurement_role(value) -> bool:
    return bool(_PROCUREMENT.search(_clean(value))) and (is_job_title(value) or is_department(value))


def has_supplier_intent(value) -> bool:
    text = _clean(value)
    # Article/comparison titles are not calls to apply or contact a department.
    if re.match(r'^(?:why|how|compare|comparing|benefits? of|best)\b', text, re.I):
        return False
    return bool(_SUPPLIER.search(text) and _SUPPLIER_ACTION.search(text))


def invalid_contact_reason(contact) -> str | None:
    """Return a field-quality reason for archive review, without mutating input.

    Missing provenance is not itself an archive reason: older imported contacts
    may lack it. Historical roles remain valid evidence of historical employment.
    """
    def field(key):
        try:
            return contact[key]
        except (KeyError, IndexError, TypeError):
            return None

    name, role, email = field('name'), field('role'), field('email')
    if reason := invalid_contact_channel_reason(field('kind'), email, field('url'), field('evidence')):
        return reason
    vendor_domains = {'starapps.studio', 'accessibe.com'}
    mail_domain = str(email or '').casefold().split('@')[-1]
    try:
        source_host = (urlsplit(field('source_url') or '').hostname or '').lower()
    except ValueError:
        source_host = ''
    if mail_domain in vendor_domains and source_host and not (
            source_host == mail_domain or source_host.endswith('.' + mail_domain)):
        return 'third_party_service_email'
    if name and (
        re.match(r'^(?:this website|we |our |you |accept cookies|cookie )', _clean(name), re.I)
        or re.search(r'\s[-–—]\s*(?:EVP|COO|CEO|CFO|President|Director|Manager)\b', _clean(name), re.I)
    ):
        return 'invalid_person_name'
    if role and re.search(r'\b(?:buyer|manufacturer)\s+(?:join|login|sign up)\b', role, re.I):
        return 'invalid_job_title'
    name_words = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', _clean(name)).split()
    if name and not is_person_name(name) and (
            not isinstance(name, str) or '@' in name or '<' in name or
            sum(w.casefold() in _NON_NAME for w in name_words) >= 2 or
            is_department(name) or
            re.match(r'^(why|how|get|send)\b', name, re.I) or
            re.search(r'公司|团队|團隊|部门|部門|服务|服務|质量|質量', name)):
        return 'invalid_person_name'
    if role and re.fullmatch(r'\s*fabric(?:\s*(?:and|&)\s*trim)?\s+sourcing\s*', role, re.I):
        return 'invalid_job_title'
    if role and not (is_job_title(role) or (not name and is_department(role))) and (
            not isinstance(role, str) or re.search(
                r'@|<|\b(why|instead|protection|solutions?|options?|plan|runs|realistic|'
                r'email|contact|compare|discover|learn|we|our|your|you)\b|^\s*tags?\s*:', role, re.I)):
        return 'invalid_job_title'
    if (field('kind') == 'procurement' and is_job_title(role)
            and not is_procurement_role(role)):
        return 'unsupported_procurement'
    if name and email and isinstance(email, str):
        local = email.split('@', 1)[0]
        if GENERAL_LOCAL.fullmatch(local) or DEPARTMENT_LOCAL.fullmatch(local):
            return 'shared_mailbox_assigned_to_person'
    if field('kind') == 'supplier_form' and not has_supplier_intent(field('evidence')):
        return 'supplier_link_without_contact_intent'
    return None


def contact_quality(contact) -> str:
    """Pure strict quality check: "" if usable, otherwise a stable reason code.

    ``invalid_contact_reason`` remains the backwards-compatible, more cautious
    legacy archive policy returning None for valid records. This stricter entry
    point does not decide review status, affiliation, or email deliverability.
    Both functions accept mappings/sqlite rows and never mutate them or do IO.
    """
    values = dict(contact)
    name = values.get('name')
    role = values.get('role')
    email = str(values.get('email') or '')
    evidence = str(values.get('evidence') or '')
    if name and not is_person_name(name):
        return 'non_person_name'
    if role and not (is_job_title(role) or (not name and is_department(role))):
        return 'invalid_job_title'
    if values.get('kind') == 'supplier_form' and not has_supplier_intent(evidence):
        return 'not_supplier_application'
    if values.get('kind') == 'procurement' and not (
        is_procurement_role(role) or
        (not name and DEPARTMENT_LOCAL.fullmatch(email.split('@')[0]))
    ):
        return 'unsupported_procurement'
    if name and not (email or role or values.get('url')) and not (
        str(name).casefold() in evidence.casefold() and
        any(marker in evidence for marker in ('JSON-LD Person:', 'Microdata Person:', 'Person block ('))
    ):
        return 'unsupported_person_context'
    if not any(values.get(key) for key in ('name', 'email', 'url')):
        return 'missing_contact'
    return invalid_contact_reason(values) or ''
