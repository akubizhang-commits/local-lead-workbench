"""Extractor quality regression fixtures: synthetic HTML, no DB/network."""
import json

import pytest

from leadworkbench.sources.website import parse_page

SOURCE = 'https://fixture.example/team'


def contacts(html):
    return parse_page(html, SOURCE)['contacts']


@pytest.mark.parametrize('name,role', [
    ('FashionAtlas QualityControl', 'Our sourcing services help buyers find the best suppliers'),
    ('Why Buyers Use FashionAtlas', 'Buyer protection'),
    ('WhyBuyersUseFashionAtlas', 'Buyer protection'),
    ('Quality Control', 'Sourcing solutions for your business'),
    ('Global Sourcing', 'Procurement Manager'),
])
def test_marketing_cards_never_become_people(name, role):
    html = f'<div class="person"><h3>{name}</h3><p class="role">{role}</p></div>'
    assert contacts(html) == []


def test_repeated_screenshot_cards_do_not_create_65_contacts():
    html = ('<div class="team-card"><h3>Why Buyers Use FashionAtlas</h3>'
            '<p class="role">Buyer protection</p></div>') * 65
    assert contacts(html) == []


@pytest.mark.parametrize('role', [
    'We help buyers source the best products',
    'Our sourcing team helps you grow',
    'Buyer protection', 'Global sourcing services',
])
def test_marketing_sentence_is_not_job_title_or_procurement_evidence(role):
    rows = contacts(f'<div class="person"><h3>Jane Doe</h3><p>{role}</p>'
                    '<a href="mailto:jane@fixture.example">Email</a></div>')
    assert len(rows) == 1
    assert rows[0]['role'] is None
    assert rows[0]['kind'] == 'general'
    assert rows[0]['role_status'] == 'unknown'


def test_generic_article_heading_is_not_person_even_with_role_word():
    rows = contacts('<article><h3>Jane Doe</h3><p class="role">Buyer</p>'
                    '<p>Learn how buyers use our platform.</p><p>press@fixture.example</p></article>')
    assert len(rows) == 1
    assert rows[0]['name'] is None
    assert rows[0]['kind'] == 'general'


def test_sourcing_text_around_unrelated_mailbox_not_procurement():
    rows = contacts('<div>Our sourcing service supports buyers. <a href="mailto:desk@fixture.example">Email</a></div>')
    assert len(rows) == 1
    assert rows[0]['kind'] == 'general'
    assert rows[0]['role'] is None


def test_supplier_service_links_are_not_application_forms():
    rows = contacts('<a href="/sourcing">Supplier sourcing services</a>'
                    '<a href="/vendors">Our vendors</a>'
                    '<a href="/apply">Supplier application</a>'
                    '<a href="/portal">Vendor registration portal</a>')
    assert {c['url'] for c in rows} == {'https://fixture.example/apply', 'https://fixture.example/portal'}
    assert all(c['kind'] == 'supplier_form' for c in rows)


def test_generic_service_enquiry_form_not_supplier_application():
    rows = contacts('<form action="/enquiry"><h2>Supplier sourcing services</h2>'
                    '<input name="email"><button>Get a quote</button></form>')
    assert not any(c['kind'] == 'supplier_form' for c in rows)


def test_jsonld_person_before_script_removal_and_case_dedup():
    data = {'@context': 'https://schema.org', '@graph': [
        {'@type': 'Organization', 'name': 'Fixture', 'email': 'ignored@fixture.example'},
        {'@type': 'Person', 'name': 'Jane Doe', 'jobTitle': 'Senior Buyer', 'email': 'jane@fixture.example',
         'worksFor': {'url': 'https://fixture.example/'}},
    ]}
    html = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    html += '<script>const email="secret@fixture.example";</script>'
    html += '<div class="person"><h3>Jane Doe</h3><p>Senior Buyer</p><a href="mailto:JANE@fixture.example">Email</a></div>'
    rows = contacts(html)
    assert len(rows) == 1
    assert rows[0]['name'] == 'Jane Doe'
    assert rows[0]['role'] == 'Senior Buyer'
    assert rows[0]['kind'] == 'procurement'
    assert rows[0]['source_url'] == SOURCE
    assert 'Jane Doe' in rows[0]['evidence'] and 'Senior Buyer' in rows[0]['evidence']
    assert 'secret@' not in str(rows) and 'ignored@' not in str(rows)


@pytest.mark.parametrize('person', [
    {'@type':'Person', 'name':'Élodie Martin', 'jobTitle':'Textile Technologist'},
    {'@type':['Thing','Person'], 'name':'王小明'},
])
def test_jsonld_affiliated_person_without_email_or_role_preserved(person):
    person = {**person, 'worksFor': {'url': 'https://fixture.example/'}}
    rows = contacts('<script type="application/ld+json">'+json.dumps(person)+'</script>')
    assert len(rows) == 1
    assert rows[0]['name'] == person['name']
    assert rows[0]['role'] == person.get('jobTitle')
    assert rows[0]['kind'] == 'general'


def test_malformed_jsonld_does_not_block_visible_contacts():
    rows = contacts('<script type="application/ld+json">{"broken":</script>'
                    '<div class="employee"><h3>Anna Müller</h3><p>Quality Control Manager</p></div>')
    assert len(rows) == 1
    assert rows[0]['name'] == 'Anna Müller'
    assert rows[0]['role'] == 'Quality Control Manager'
    assert rows[0]['kind'] == 'general'


def test_affiliated_person_context_preserves_roleless_and_emailless_name():
    rows = contacts('<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">Prince</span>'
                    '<a itemprop="worksFor" href="https://fixture.example/">Fixture</a></div>')
    assert len(rows) == 1
    assert rows[0]['name'] == 'Prince'
    assert rows[0]['role'] is None and rows[0]['email'] is None
    assert rows[0]['kind'] == 'general'


def test_unscoped_name_without_channel_or_role_is_not_person():
    assert contacts('<div><h3>Jane Doe</h3></div>') == []


def test_duplicate_cards_and_mailto_text_are_one_person_per_page():
    card = '<div class="person"><h3>Jane Doe</h3><p>Buyer</p><a href="mailto:JANE@fixture.example">jane@fixture.example</a></div>'
    rows = contacts(card * 3)
    assert len(rows) == 1
    assert rows[0]['name'] == 'Jane Doe'


def test_legacy_quality_contract_pure_and_stable_reasons():
    from leadworkbench.contact_quality import contact_quality
    valid = dict(name='Jane Doe', role='Buyer', email='jane@fixture.example', kind='procurement',
                 role_status='explicit', association_status='same_block', source_url=SOURCE,
                 evidence='Jane Doe Buyer jane@fixture.example')
    before = dict(valid)
    assert contact_quality(valid) == ''
    assert valid == before
    assert contact_quality({**valid, 'name':'FashionAtlas QualityControl'}) == 'non_person_name'
    assert contact_quality({**valid, 'role':'We help buyers source the best products'}) == 'invalid_job_title'
    assert contact_quality({**valid, 'role':'Sales Manager'}) == 'unsupported_procurement'
    assert contact_quality({**valid, 'name':'', 'role':'', 'email':'desk@fixture.example',
                            'kind':'general', 'association_status':'company_only'}) == ''
    assert contact_quality(dict(kind='supplier_form', url='https://fixture.example/sourcing',
                                evidence='Supplier sourcing services')) == 'not_supplier_application'


@pytest.mark.parametrize('html', [
    '<div class="person"><h3>Jane Doe</h3></div>',
    '<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">Prince</span>'
    '<a itemprop="worksFor" href="https://fixture.example/">Fixture</a></div>',
    '<script type="application/ld+json">{"@type":"Person","name":"王小明",'
    '"worksFor":{"url":"https://fixture.example/"}}</script>',
])
def test_retained_strong_roleless_people_pass_public_validator(html):
    from leadworkbench.contact_quality import contact_quality
    rows = contacts(html)
    assert len(rows) == 1
    assert contact_quality(rows[0]) == ''


def test_both_quality_entrypoints_support_sqlite_rows_without_io():
    # A Row is a supported migration input; the only DB is an in-memory fixture.
    import sqlite3
    from leadworkbench.contact_quality import contact_quality, invalid_contact_reason
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT 'Jane Doe' AS name, 'Buyer' AS role, 'procurement' AS kind"
        ).fetchone()
    assert contact_quality(row) == ''
    assert invalid_contact_reason(row) is None


def test_standalone_structured_person_is_not_employment_evidence():
    person = {'@type': 'Person', 'name': 'Prince'}
    assert contacts('<script type="application/ld+json">' + json.dumps(person) + '</script>') == []
    assert contacts('<div itemscope itemtype="https://schema.org/Person">'
                    '<span itemprop="name">Prince</span></div>') == []
