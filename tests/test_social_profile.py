from leadworkbench.db import Store


def test_search_queues_profiles_not_posts(tmp_path):
 s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id']
 run=s.create_social_run([cid],platform_search=True);item=s.claim_social_item()
 s.finish_social_item(item['id'],{'status':'succeeded','links':[{'url':'https://facebook.com/brand/','evidence':'Brand','source_url':'https://facebook.com/search/','method':'platform_search'}]})
 jobs=s.social_run(run['id'])['items']
 assert any(j['stage']=='profile' for j in jobs)
 assert not any(j['stage']=='read' for j in jobs)


def test_x_profile_does_not_extract_contacts_from_posts():
 from leadworkbench.sources.social_profile import parse_profile
 html='''<main><div data-testid="UserName">Brand @brand</div><div data-testid="UserDescription">Wholesale. Email hello@brand.example</div><span data-testid="UserLocation">Los Angeles</span><a data-testid="UserUrl" href="https://brand.example">brand.example</a><article>other@unrelated.example</article></main>'''
 r=parse_profile(html,'https://x.com/brand')
 assert r['status']=='preview_read'
 assert any(f['key']=='location' and f['value']=='Los Angeles' for f in r['fields'])
 assert any(f['value']=='hello@brand.example' for f in r['fields'])
 assert 'other@unrelated.example' not in str(r)
 assert r['posts']==[]


def test_profile_only_task_and_retry(tmp_path):
 s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id']
 s.add_social(cid,'https://x.com/brand','test')
 r=s.create_social_run([cid],profile_only=True)
 assert [i['stage'] for i in r['items']]==['profile']
 s.cancel_social_run(r['id'])
 assert s.retry_social_run(r['id'])['items'][0]['stage']=='profile'


def test_matching_facts_do_not_confirm_ownership():
 from leadworkbench.sources.social_profile import profile_match
 company={'website':'https://www.brand.example','phone':'+1 213 123 4567'}
 fields=[{'key':'website','value':'https://brand.example/shop','source_url':'https://x.com/brand'}, {'key':'phone','value':'+1 (213) 123-4567','source_url':'https://x.com/brand'}]
 assert len(profile_match(company,fields))==2
 assert profile_match(company,[{'key':'website','value':'https://brand.example.evil.test'}])==[]


def test_instagram_header_becomes_fields_not_button_text():
 from leadworkbench.sources.social_profile import parse_profile
 html='<main><header><h2>ambiance_wholesale</h2><div>选项</div><div>Ambiance Apparel</div><div>28</div><div>帖子</div><div>2796</div><div>粉丝</div><div>166</div><div>关注</div><div>Women Clothing</div><div>ambianceapparel.com</div><div>关注</div><div>发消息</div></header></main>'
 r=parse_profile(html,'https://www.instagram.com/ambiance_wholesale/')
 assert r['title']=='Ambiance Apparel'
 assert r['description']=='Women Clothing'
 assert any(f['key']=='followers' and f['value']=='2796' for f in r['fields'])
 assert any(f['key']=='website_text' and f['value']=='ambianceapparel.com' for f in r['fields'])


def test_profile_refresh_preserves_deduplicated_posts(tmp_path):
 import json
 s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id']
 entry=s.add_social(cid,'https://x.com/brand','test');run=s.create_social_run([cid])
 with s.connect() as con:
  for i,posts in enumerate([[{'url':'https://x.com/brand/status/1','text':'old'}],[{'url':'https://x.com/brand/status/1','text':'updated'}],[]]):
   con.execute('INSERT INTO social_page_observations VALUES(?,?,?,?,?)',(str(i),entry['id'],run['id'],json.dumps({'status':'preview_read','posts':posts,'title':'Brand'}),f'2026-09-2{i}'))
 page=s.social_pages(cid)[0]
 assert len(page['posts'])==1
 assert page['posts'][0]['text']=='updated'
 assert page['posts'][0]['observed_at']=='2026-09-21'
