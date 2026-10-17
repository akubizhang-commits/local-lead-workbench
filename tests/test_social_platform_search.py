import pytest
from leadworkbench.db import Store


def test_platform_search_without_website_or_accounts(tmp_path):
    s = Store(tmp_path)
    cid = s.manual_company('Vocal Apparel', 'https://example.org')['id']
    run = s.create_social_run([cid], platform_search=True)
    assert [i['stage'] for i in run['items']] == ['platform_search', 'platform_search', 'platform_search']
    assert {i['target'] for i in run['items']} == {'facebook', 'instagram', 'x'}
    s.cancel_social_run(run['id'])
    retry = s.retry_social_run(run['id'])
    assert all(i['stage'] == 'platform_search' for i in retry['items'])


def test_search_candidates_filter_navigation_posts_and_unrelated_names():
    from leadworkbench.sources.social_platform_search import parse_candidates
    html = '''<nav><a href="/myprofile/">My Profile</a></nav><main>
    <a href="/vocalapparel/">Vocal Apparel Los Angeles</a>
    <a href="/vocalapparel/?hl=en">Vocal Apparel</a>
    <a href="/someone/">Unrelated Person</a>
    <a href="/p/abc/">Vocal Apparel</a></main>'''
    result = parse_candidates(html, 'instagram', 'Vocal Apparel', 'https://www.instagram.com/search/')
    assert len(result['links']) == 1
    assert result['links'][0]['url'] == 'https://www.instagram.com/vocalapparel/'
    assert result['links'][0]['method'] == 'platform_search'
    assert parse_candidates('<main></main>', 'instagram', 'Vocal', '')['status'] == 'blocked'


@pytest.mark.asyncio
async def test_worker_search_persists_only_candidates_with_source(tmp_path):
    from leadworkbench.social_worker import process_social_item
    s = Store(tmp_path)
    cid = s.manual_company('Vocal Apparel', 'https://example.org')['id']
    s.create_social_run([cid], platform_search=True)
    async def search(platform, query, directory, settings):
        assert query == 'Vocal Apparel'
        return {'status':'succeeded', 'links':[{
            'url':'https://www.facebook.com/vocalapparelofficial/',
            'source_url':'https://www.facebook.com/search/pages/?q=Vocal+Apparel',
            'evidence':'企业名称站内搜索候选，归属未确认', 'method':'platform_search'}]}
    await process_social_item(s, platform_search=search)
    assert s.social_detail(cid)['accounts'][0]['status'] == 'candidate'


def test_x_has_own_session_and_search_task(tmp_path):
    from leadworkbench.social_connections import public_connections
    from leadworkbench.sources.social_browser import profile_path
    assert 'x' in public_connections(tmp_path)['sessions']
    assert profile_path(tmp_path,'x').name == 'x'
    s=Store(tmp_path);cid=s.manual_company('Vocal Apparel','https://example.org')['id']
    assert 'x' in {i['target'] for i in s.create_social_run([cid],platform_search=True)['items']}


@pytest.mark.asyncio
async def test_x_browser_routing(tmp_path,monkeypatch):
    from leadworkbench.sources import social_browser,social_combination
    async def read(url,directory,settings):return {'status':'auth_required','provider':'browser'}
    monkeypatch.setattr(social_browser,'read_browser',read)
    assert (await social_combination.read_combined('https://twitter.com/vocalapparel',tmp_path,{'reader':'browser'}))['provider']=='browser'
