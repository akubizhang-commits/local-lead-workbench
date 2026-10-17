from leadworkbench.db import Store


def test_new_contact_saved_without_review_and_conflict_has_real_values(tmp_path):
    s=Store(tmp_path);a=s.manual_company('Fixture','https://fixture.example')
    c=dict(name='Alex',email='alex@fixture.example',role='',source_url='https://fixture.example/team',evidence='Alex email')
    s.apply_enrichment(a['id'],dict(status='completed',contacts=[c]))
    assert not s.reviews()
    assert s.detail(a['id'])['contacts'][0]['review_status']=='auto_recorded'
    s.apply_enrichment(a['id'],dict(status='completed',contacts=[{**c,'role':'Buyer'}]))
    assert not s.reviews()
    s.apply_enrichment(a['id'],dict(status='completed',contacts=[{**c,'role':'Sales'}]))
    r=s.reviews()[0]
    assert r['contact']['email']=='alex@fixture.example'
    assert r['previous_contact']['role']=='Buyer'


def test_manual_blank_field_fills_without_review_and_legacy_items_resolve(tmp_path):
    s=Store(tmp_path);a=s.manual_company('Fixture','https://fixture.example')
    s.update_company(a['id'],a['revision'],{'phone':'123456789'},'',None)
    a=s.company(a['id']);s.update_company(a['id'],a['revision'],{'phone':''},'',None)
    s.apply_enrichment(a['id'],dict(status='completed',observations=[dict(field='phone',value='987654321',source_url='https://fixture.example',evidence='Phone')]))
    assert s.company(a['id'])['phone']=='987654321'
    assert not s.reviews()


def test_old_empty_reviews_resolve_without_overwriting_populated_field(tmp_path):
    s=Store(tmp_path);a=s.manual_company('Fixture','https://fixture.example')
    c=dict(name='Alex',email='alex@fixture.example',role='',source_url='https://fixture.example/team',evidence='email')
    s.apply_enrichment(a['id'],dict(status="completed",contacts=[c]))
    contact=s.detail(a['id'])['contacts'][0]
    with s.connect() as con:
        con.execute("UPDATE contacts SET review_status='pending' WHERE id=?",(contact['id'],))
        s._review(con,a['id'],'contact','contact','',contact['id'],c['source_url'],'email')
        s._review(con,a['id'],'field_update','phone','','11111111',c['source_url'],'phone')
    assert s.resolve_empty_reviews()==2
    assert s.company(a['id'])['phone']=='11111111'
    assert s.detail(a['id'])['contacts'][0]['review_status']=='auto_recorded'
    with s.connect() as con:
        s._review(con,a['id'],'field_update','phone','','22222222',c['source_url'],'other')
    assert s.resolve_empty_reviews()==0
    assert s.company(a['id'])['phone']=='11111111'
