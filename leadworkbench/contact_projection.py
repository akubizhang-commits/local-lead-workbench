"""Contact display projection; original source records remain intact."""
from .contact_fields import contact_fields
from .contact_quality import invalid_email_reason, non_outreach_email_reason, invalid_contact_channel_reason
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HIDDEN_STATUSES = {'reject', 'invalid', 'superseded'}


def _norm(value):
    return ' '.join((value or '').split()).casefold()


def outreach_category(kind, role_status, email, role):
    if (invalid_email_reason(email) or non_outreach_email_reason(email)
            or re.search(r'\b(recruiter|recruitment|human resources|talent acquisition)\b|招聘|人力资源', role or '', re.I)):
        return 'irrelevant'
    if kind == 'procurement' and role_status in {'explicit', 'department'}:
        return 'procurement'
    return 'email' if _norm(email) else 'other'


def categorize_outreach(records):
    """One mailbox belongs to one category; retain all supporting sources."""
    usable = [r for r in records if r.get('review_status') not in HIDDEN_STATUSES | {'pending'}
              and r.get('adoption_status') != 'excluded']
    buyers = {_norm(r.get('email')) for r in usable if _norm(r.get('email'))
              and outreach_category(r.get('kind'),r.get('role_status'),r.get('email'),r.get('role')) == 'procurement'}
    groups = {}
    result = []
    for record in sorted(records, key=lambda r: outreach_category(r.get('kind'),r.get('role_status'),r.get('email'),r.get('role')) != 'procurement'):
        record = {**record}
        category = outreach_category(record.get('kind'),record.get('role_status'),record.get('email'),record.get('role'))
        email = _norm(record.get('email'))
        eligible = record.get('review_status') not in HIDDEN_STATUSES | {'pending'} and record.get('adoption_status') != 'excluded'
        if eligible and email and email in buyers and category != 'irrelevant':
            category = 'procurement'
        record['outreach_category'] = category
        key = (email, category) if eligible and email else None
        if key and key in groups:
            group = groups[key]
            for attr in ('sources','fields','record_ids'):
                group[attr] = group.get(attr,[]) + [v for v in record.get(attr,[]) if v not in group.get(attr,[])]
        else:
            result.append(record)
            if key:groups[key] = record
    return result


def contact_key(contact):
    # Different names, roles or channels are not interchangeable. Without a
    # channel, an explicit name+role is the minimum supported identity.
    keys = ('company_id', 'name', 'role', 'email', 'url', 'kind', 'role_status', 'association_status')
    values = {k: _norm(contact.get(k)) for k in keys}
    if values['url']:
        try:
            parts = urlsplit(values['url'])
            tracking = {'fbclid', 'gclid', 'referrerpage', 'refpgid'}
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                     if k not in tracking and not k.startswith('utm_')]
            values['url'] = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/') or '/',
                                       urlencode(sorted(query)), parts.fragment))
        except ValueError:
            pass
    key = tuple(values[k] for k in keys)
    if not (contact.get('email') or contact.get('url') or
            (contact.get('name') and contact.get('role'))):
        key += (contact.get('id'),)
    return key


def group_contact_records(records, website='', *, include_excluded=False):
    groups = {}
    for record in sorted(records, key=lambda c: c.get('observed_at') or '', reverse=True):
        # Historical and manually reviewed records also pass the same channel
        # gate. Evidence stays in storage; an invalid mailbox is never a CTA.
        if invalid_contact_channel_reason(record.get('kind'), record.get('email'),
                                          record.get('url'), record.get('evidence')):
            continue
        excluded = record.get('adoption_status') == 'excluded' or record.get('review_status') == 'reject'
        if record.get('review_status') in {'invalid','superseded'} or (excluded and not include_excluded):
            continue
        key = contact_key(record) + (excluded,)
        if key not in groups:
            groups[key] = {**record, 'fields': [], 'sources': [], 'record_ids': []}
        group = groups[key]
        if record['id'] not in group['record_ids']:
            group['record_ids'].append(record['id'])
        source = {k: record.get(k) or '' for k in ('source_url', 'evidence', 'observed_at')}
        if source not in group['sources']:
            group['sources'].append(source)
        # Keep conflicting field values visible, each tied to its own source.
        seen = {(f['key'], f['value']) for f in group['fields']}
        for field in contact_fields(record, website):
            if (field['key'], field['value']) not in seen:
                group['fields'].append(field)
                seen.add((field['key'], field['value']))
        if record.get('review_status') == 'pending':
            group['review_status'] = 'pending'
    return list(groups.values())


VERSION_IDENTITY = ('name', 'role', 'email', 'kind', 'role_status', 'association_status')


def merge_versions(rows, versions):
    """Current contacts plus earlier observations of the *same* identity as extra sources.

    ``versions`` maps contact_id to payload dicts ordered by observation time. A
    version is attached only when all identity keys match, so Alex's phone or job
    never appears under Bob just because they shared a mailbox."""
    records = []
    for current in rows:
        records.append(current)
        for payload in versions.get(current['id'], ()):
            if all((payload.get(k) or '') == (current.get(k) or '') for k in VERSION_IDENTITY):
                records.append({**current, **{k: payload.get(k) or current.get(k)
                                              for k in ('source_url', 'evidence', 'observed_at')}})
    return records
