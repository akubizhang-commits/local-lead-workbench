import pytest
from leadworkbench.db import Store


def pair(store):
    a = store.manual_company('Branch Store', 'https://www.brand.example/')
    b = store.manual_company('Branch Store', 'http://brand.example')
    store.update_company(a['id'], a['revision'], {'address': '1 First St', 'phone': '111'}, '', None)
    store.update_company(b['id'], b['revision'], {'address': '2 Second St', 'phone': '222'}, '', None)
    with store.connect() as con:
        store._review(con, b['id'], 'duplicate_candidate', 'company', a['id'], b['id'], 'https://maps.google.com', '同名来源证据不足；不自动合并')
    return a, b


def test_duplicate_review_exposes_comparable_companies_and_keep_separate(tmp_path):
    store = Store(tmp_path)
    a, b = pair(store)
    review = store.reviews()[0]
    assert review['comparison']['left']['address'] == '1 First St'
    assert review['comparison']['right']['phone'] == '222'
    assert review['comparison']['same_website'] is True
    with pytest.raises(ValueError, match='独立记录'):
        store.review_decision(review['id'], 'accept')
    store.review_decision(review['id'], 'reject')
    assert store.reviews() == []
    assert store.company(a['id'])['phone'] == '111'
    assert store.company(b['id'])['phone'] == '222'
    assert [c['id'] for c in store.detail(a['id'])['related_locations']] == [b['id']]


def test_website_groups_do_not_join_empty_or_different_tenant_paths(tmp_path):
    store = Store(tmp_path)
    a = store.manual_company('One', 'https://host.example/one')
    store.manual_company('Two', 'https://host.example/two')
    empty = store.manual_company('Empty', '')
    store.manual_company('Empty too', '')
    assert store.detail(a['id'])['related_locations'] == []
    assert store.detail(empty['id'])['related_locations'] == []
