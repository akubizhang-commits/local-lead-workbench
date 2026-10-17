"""Offline contracts for optional public social readers."""
import asyncio
import importlib
import json
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

IG = 'https://www.instagram.com/example/'
YT = 'https://www.youtube.com/@Example'
CID = 'UC' + 'a' * 22


@pytest.fixture
def mod():
    try:
        return importlib.import_module('leadworkbench.sources.social_optional')
    except ModuleNotFoundError:
        pytest.fail('Optional social provider module has not been implemented')


@pytest.fixture(autouse=True)
def prohibit_live_provider_io(monkeypatch):
    monkeypatch.setattr(aiohttp, 'ClientSession', Mock(side_effect=AssertionError('Network must be mocked')))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(side_effect=AssertionError('Child must be mocked')))


@pytest.fixture
def secrets(tmp_path):
    (tmp_path / 'social-secrets.json').write_text(json.dumps({'youtube_api_key': 'SECRET-KEY'}))
    return tmp_path


class Response:
    def __init__(self, data, status=200, raw=None):
        self.status = status
        self.raw = json.dumps(data).encode() if raw is None else raw
        self.content_length = None
        self.content = self

    async def iter_chunked(self, size):
        for start in range(0, len(self.raw), size):
            yield self.raw[start:start + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def http(monkeypatch, mod, responses):
    session = Mock()
    session.get.side_effect = responses
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = Mock(return_value=session)
    monkeypatch.setattr(mod.aiohttp, 'ClientSession', factory)
    return session, factory


def channel():
    return {'items': [{'id': CID, 'snippet': {'title': 'Example', 'description': 'Public bio'},
                       'contentDetails': {'relatedPlaylists': {'uploads': 'UU' + 'a' * 22}}}]}


def videos(n=8):
    return {'items': [{'snippet': {'title': f'Video {i}', 'description': 'Caption',
                                   'resourceId': {'videoId': f'{i:011d}'}, 'publishedAt': 'playlist-time'},
                       'contentDetails': {'videoPublishedAt': '2026-09-01T00:00:00Z'}} for i in range(n)]}


@pytest.mark.parametrize('path,filter,value', [('@Example', 'forHandle', '@Example'),
    ('channel/' + CID, 'id', CID), ('user/Example', 'forUsername', 'Example')])
async def test_youtube_resolution_posts_and_proxy(mod, monkeypatch, secrets, path, filter, value):
    session, factory = http(monkeypatch, mod, [Response(channel()), Response(videos())])
    result = await mod.read_youtube('https://www.youtube.com/' + path, secrets,
                                    {'max_posts': 99, 'proxy': {'server': 'http://127.0.0.1:7890'}})
    assert result['status'] == 'preview_read'
    assert result['title'] == 'Example' and result['description'] == 'Public bio'
    assert len(result['posts']) == 5 and result['fields'] == []
    assert result['posts'][0] == {'url': 'https://www.youtube.com/watch?v=00000000000',
                                  'text': 'Video 0\nCaption', 'published_at': '2026-09-01T00:00:00Z'}
    calls = session.get.call_args_list
    assert calls[0].kwargs['params'][filter] == value
    assert calls[1].kwargs['params']['maxResults'] == 5
    assert calls[1].args[0].endswith('/playlistItems')
    assert all(c.kwargs['allow_redirects'] is False for c in calls)
    assert all(c.kwargs['proxy'] == 'http://127.0.0.1:7890' for c in calls)
    assert factory.call_args.kwargs['trust_env'] is False


@pytest.mark.parametrize('reader,url', [('read_instaloader', u) for u in [
    'http://www.instagram.com/example/', IG + '?x=1', IG + '#x',
    'https://www.instagram.com.evil/example/', 'https://u:p@www.instagram.com/example/',
    'https://www.instagram.com:443/example/', 'https://www.instagram.com/p/abc/',
    'https://www.instagram.com//example/', 'https://www.instagram.com/%65xample/',
    'https://www.instagram.com/accounts/', ' https://www.instagram.com/example/']]
    + [('read_youtube', u) for u in ['https://youtu.be/abc', YT + '/videos', YT + '?key=secret',
       'https://www.youtube.com/c/Example', 'https://www.youtube.com/channel/nope']])
async def test_rejects_noncanonical_urls(mod, tmp_path, reader, url):
    result = await getattr(mod, reader)(url, tmp_path, {})
    assert result['status'] == 'blocked' and result['reason'] == 'unsupported_url'
    assert result['posts'] == []


async def test_missing_key(mod, tmp_path):
    r = await mod.read_youtube(YT, tmp_path, {})
    assert r['status'] == 'blocked' and r['reason'] == 'not_configured'
    assert 'youtube_api_key' in r['message']


@pytest.mark.parametrize('status,reason,expected', [(403, 'quotaExceeded', 'quota_exceeded'),
    (400, 'keyInvalid', 'auth_required'), (401, 'authError', 'auth_required'),
    (403, 'forbidden', 'access_denied'), (429, 'rateLimitExceeded', 'rate_limited'),
    (503, 'backendError', 'network_error')])
@pytest.mark.parametrize('partial', [False, True])
async def test_youtube_failure_classification_and_redaction(mod, monkeypatch, secrets, caplog, capsys,
                                                           status, reason, expected, partial):
    error = Response({'error': {'message': 'SECRET-KEY', 'errors': [{'reason': reason}]}}, status)
    http(monkeypatch, mod, ([Response(channel())] if partial else []) + [error])
    r = await mod.read_youtube(YT, secrets, {})
    assert r['reason'] == {'auth_required': 'access_denied', 'quota_exceeded': 'rate_limited'}.get(expected, expected)
    if expected in {'auth_required', 'quota_exceeded'}:
        assert r['provider_reason'] == expected
    assert r['status'] == ('preview_read' if partial else 'failed' if expected == 'network_error' else 'blocked')
    if partial:
        assert r['title'] == 'Example' and r['partial'] is True
        assert '帖子' in r['message']
    assert 'SECRET-KEY' not in json.dumps(r) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize('response,reason', [(Response({}, raw=b'x' * 1_000_001), 'response_too_large'),
    (Response({}, raw=b'not JSON'), 'invalid_response'), (Response({'items': None}), 'invalid_response'),
    (aiohttp.ClientConnectionError('SECRET-KEY'), 'network_error'),
    (TimeoutError('SECRET-KEY'), 'network_error'), (Response({'items': []}), 'not_found')])
async def test_youtube_bad_responses(mod, monkeypatch, secrets, response, reason):
    http(monkeypatch, mod, [response])
    r = await mod.read_youtube(YT, secrets, {})
    assert r['reason'] == reason and 'SECRET-KEY' not in json.dumps(r)


@pytest.mark.parametrize('proxy', ['http://evil.example:7890', 'http://u:p@localhost:7890',
                                  'http://127.0.0.1:7890/path'])
async def test_nonlocal_proxy_blocked(mod, secrets, proxy):
    for reader, url in [(mod.read_youtube, YT), (mod.read_instaloader, IG)]:
        r = await reader(url, secrets, {'proxy': {'server': proxy}})
        assert r['reason'] == 'invalid_settings'


class Child:
    def __init__(self, records=(), hang=False, stubborn=False):
        self.stdout = asyncio.StreamReader()
        for record in records:
            self.stdout.feed_data(json.dumps(record).encode() + b'\n')
        if not hang:
            self.stdout.feed_eof()
        self.returncode = None if hang else 0
        self.terminate = Mock(side_effect=self.stop if not stubborn else None)
        self.kill = Mock(side_effect=self.stop)
        self.wait = AsyncMock(side_effect=self.wait_for_exit)

    def stop(self):
        self.returncode = -15
        self.stdout.feed_eof()

    async def wait_for_exit(self):
        if self.returncode is None:
            await asyncio.Future()
        return self.returncode


def preview():
    return dict(status='preview_read', provider='instaloader', title='Example', description='Bio',
                fields=[], source_url=IG, posts=[], message='已读取公开账号预览。')


async def test_instaloader_missing_dependency(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda name: None)
    r = await mod.read_instaloader(IG, tmp_path, {})
    assert r['status'] == 'blocked' and r['reason'] == 'dependency_missing'
    assert 'Instaloader' in r['message']


@pytest.mark.parametrize('partial,stubborn', [(False, False), (True, False), (True, True)])
async def test_instaloader_timeout_terminates_reaps_preserves_profile(mod, monkeypatch, tmp_path, partial, stubborn):
    child = Child([preview()] if partial else [], hang=True, stubborn=stubborn)
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(mod.asyncio, 'create_subprocess_exec', AsyncMock(return_value=child))
    assert mod.INSTALOADER_TIMEOUT == 60
    monkeypatch.setattr(mod, 'INSTALOADER_TIMEOUT', .01)
    monkeypatch.setattr(mod, 'TERMINATE_GRACE', .01)
    r = await mod.read_instaloader(IG, tmp_path, {})
    assert r['reason'] == 'network_error'
    assert r['status'] == ('preview_read' if partial else 'failed')
    if partial:
        assert r['title'] == 'Example' and r['partial']
    child.terminate.assert_called_once()
    assert child.returncode is not None and child.wait.await_count >= 1
    if stubborn:
        child.kill.assert_called_once()


async def test_instaloader_cancellation_reaps(mod, monkeypatch, tmp_path):
    child = Child(hang=True)
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda name: object())
    spawn = AsyncMock(return_value=child)
    monkeypatch.setattr(mod.asyncio, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(mod.read_instaloader(IG, tmp_path, {}))
    while not spawn.called:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    child.terminate.assert_called_once()
    assert child.returncode is not None


@pytest.mark.parametrize('settings,count', [({}, 3), ({'max_posts': 100}, 5), ({'max_posts': 0}, 0)])
async def test_instaloader_child_contract(mod, monkeypatch, tmp_path, settings, count):
    child = Child([preview()])
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda name: object())
    spawn = AsyncMock(return_value=child)
    monkeypatch.setattr(mod.asyncio, 'create_subprocess_exec', spawn)
    r = await mod.read_instaloader(IG, tmp_path, settings)
    assert r == preview()
    args, kwargs = spawn.call_args
    assert str(count) in args and kwargs['stdin'] == asyncio.subprocess.DEVNULL
    assert kwargs['stderr'] == asyncio.subprocess.DEVNULL
    assert kwargs['env']['PYTHONDONTWRITEBYTECODE'] == '1'


def fake_instaloader(monkeypatch, private=False, failure=None):
    def posts():
        for i in range(10):
            if failure and i == 1:
                raise failure
            yield SimpleNamespace(shortcode=f'code{i}', caption=f'Caption {i}', date_utc=datetime(2026, 9, 1))
    profile = SimpleNamespace(full_name='Example', biography='Bio', is_private=private, get_posts=Mock(side_effect=posts))
    loader = Mock()
    module = SimpleNamespace(Instaloader=Mock(return_value=loader),
                             Profile=SimpleNamespace(from_username=Mock(return_value=profile)))
    monkeypatch.setitem(sys.modules, 'instaloader', module)
    return module, profile


@pytest.mark.parametrize('private,failure,reason', [(False, None, None), (True, None, 'auth_required'),
    (False, type('LoginRequiredException', (Exception,), {})('secret'), 'auth_required'),
    (False, type('ConnectionException', (Exception,), {})('secret'), 'network_error'),
    (False, type('TooManyRequestsException', (Exception,), {})('secret'), 'rate_limited')])
def test_instagram_public_only_and_partial_posts(mod, monkeypatch, private, failure, reason):
    library, profile = fake_instaloader(monkeypatch, private, failure)
    records = []
    mod._instagram_run(IG, 3, records.append)
    r = records[-1]
    if private:
        assert r['status'] == 'blocked' and r['reason'] == 'access_denied'
        assert r['provider_reason'] == reason
        profile.get_posts.assert_not_called()
    else:
        assert r['status'] == 'preview_read' and r['title'] == 'Example'
        assert len(r['posts']) == (1 if failure else 3)
        assert r['posts'][0]['published_at'] == '2026-09-01T00:00:00+00:00'
        if failure:
            assert r['partial'] and r['reason'] == {'auth_required': 'access_denied'}.get(reason, reason)
            if reason == 'auth_required':
                assert r['provider_reason'] == reason
    assert 'secret' not in json.dumps(r)
    library.Instaloader.return_value.login.assert_not_called()
    library.Instaloader.return_value.interactive_login.assert_not_called()
    library.Instaloader.return_value.load_session_from_file.assert_not_called()


async def test_instaloader_oversized_pipe_is_bounded_and_reaped(mod, monkeypatch, tmp_path):
    child = Child(hang=True)
    child.stdout.feed_data(b'x' * (mod.MAX_CHILD_OUTPUT + 1))
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(mod.asyncio, 'create_subprocess_exec', AsyncMock(return_value=child))
    r = await mod.read_instaloader(IG, tmp_path, {})
    assert r['reason'] == 'response_too_large'
    child.terminate.assert_called_once()
    assert child.returncode is not None


async def test_youtube_total_deadline_preserves_profile(mod, monkeypatch, secrets):
    class SlowResponse(Response):
        async def __aenter__(self):
            await asyncio.Future()
    http(monkeypatch, mod, [Response(channel()), SlowResponse({})])
    monkeypatch.setattr(mod, 'YOUTUBE_TIMEOUT', .01)
    r = await mod.read_youtube(YT, secrets, {})
    assert r['status'] == 'preview_read' and r['partial'] and r['reason'] == 'network_error'


@pytest.mark.parametrize('count', [0, 3])
async def test_youtube_default_and_zero_post_limit(mod, monkeypatch, secrets, count):
    session, _ = http(monkeypatch, mod, [Response(channel()), Response(videos())])
    r = await mod.read_youtube(YT, secrets, {} if count else {'max_posts': 0})
    assert len(r['posts']) == count
    assert session.get.call_count == (2 if count else 1)


@pytest.mark.parametrize('raw', ['[]', '{', '{"youtube_api_key": "SECRET KEY"}', 'x' * 65537])
async def test_malformed_secrets_are_blocked(mod, tmp_path, raw):
    (tmp_path / 'social-secrets.json').write_text(raw)
    r = await mod.read_youtube(YT, tmp_path, {})
    assert r['reason'] == 'not_configured' and 'SECRET KEY' not in json.dumps(r)


@pytest.mark.parametrize('status,body,reason', [(200, b'{}', None),
    (200, b'x' * 1_000_001, 'response_too_large'), (401, b'', 'auth_required'),
    (403, b'', 'access_denied'), (429, b'', 'rate_limited'), (503, b'', 'network_error'),
    (302, b'', 'auth_required')])
def test_instagram_transport_bounded_and_no_redirects(mod, monkeypatch, status, body, reason):
    response = Mock(status_code=status, headers={'Location': '/accounts/login/'})
    response.iter_content.return_value = iter([body])
    original = Mock(return_value=response)
    class Session:
        send = original
    fake = SimpleNamespace(sessions=SimpleNamespace(Session=Session))
    monkeypatch.setitem(sys.modules, 'requests', fake)
    with mod._bounded_instagram_transport('http://localhost:7890'):
        if reason:
            with pytest.raises(mod.ProviderError) as exc:
                Session().send(SimpleNamespace(url=IG))
            assert exc.value.reason == reason
        else:
            result = Session().send(SimpleNamespace(url=IG))
            assert result._content == b'{}'
    assert Session.send is original
    options = original.call_args.kwargs
    assert options['stream'] and options['allow_redirects'] is False
    assert options['proxies']['https'] == 'http://localhost:7890'
    assert options['timeout'] == (5, 12)
    response.close.assert_called_once()


def test_instagram_transport_rejects_external_destination(mod, monkeypatch):
    original = Mock()
    class Session:
        send = original
    monkeypatch.setitem(sys.modules, 'requests', SimpleNamespace(sessions=SimpleNamespace(Session=Session)))
    with mod._bounded_instagram_transport(None), pytest.raises(mod.ProviderError):
        Session().send(SimpleNamespace(url='https://evil.example/'))
    original.assert_not_called()


async def test_reaping_drains_paused_stdout_before_waiting(mod):
    drained = asyncio.Event()
    async def drain(size):
        drained.set()
        return b''
    async def wait():
        await drained.wait()
        return 0
    child = SimpleNamespace(returncode=0, stdout=SimpleNamespace(read=AsyncMock(side_effect=drain)),
                            wait=AsyncMock(side_effect=wait))
    await asyncio.wait_for(mod._reap(child), .05)
    assert drained.is_set()


@pytest.mark.parametrize('proxy', ['http://localhost:1024', 'http://127.0.0.1:7890', 'http://[::1]:65535'])
async def test_actual_proxy_url_reaches_both_providers(mod, monkeypatch, secrets, proxy):
    from leadworkbench.social_connections import load_connections
    (secrets / 'social-connections.json').write_text(json.dumps({'proxy_url': proxy}))
    settings = load_connections(secrets)
    settings['proxy'] = 'http://localhost:8888'
    session, _ = http(monkeypatch, mod, [Response(channel()), Response(videos())])
    result = await mod.read_youtube(YT, secrets, settings)
    assert result['provider'] == 'youtube'
    assert all(call.kwargs['proxy'] == proxy for call in session.get.call_args_list)
    monkeypatch.setattr(mod.importlib.util, 'find_spec', lambda _: object())
    spawn = AsyncMock(return_value=Child([preview()]))
    monkeypatch.setattr(mod.asyncio, 'create_subprocess_exec', spawn)
    await mod.read_instaloader(IG, secrets, settings)
    assert spawn.call_args.args[-1] == proxy


def test_explicit_empty_proxy_url_disables_alias(mod):
    assert mod._options({'proxy_url': '', 'proxy': 'http://localhost:7890'}) == (3, None)


@pytest.mark.parametrize('proxy', ['socks5://localhost:1024', 'socks5://127.0.0.1:7890', 'socks5://[::1]:7890'])
async def test_socks_proxy_blocked_before_io_with_http_guidance(mod, monkeypatch, secrets, proxy, caplog, capsys):
    session = Mock(side_effect=AssertionError('must not connect'))
    spawn = AsyncMock(side_effect=AssertionError('must not spawn'))
    monkeypatch.setattr(mod.aiohttp, 'ClientSession', session)
    monkeypatch.setattr(mod.asyncio, 'create_subprocess_exec', spawn)
    for reader, url in [(mod.read_youtube, YT), (mod.read_instaloader, IG)]:
        result = await reader(url, secrets, {'proxy_url': proxy})
        assert result['status'] == 'blocked'
        assert result['reason'] == 'unsupported_proxy'
        assert 'HTTP' in result['message'] and '代理' in result['message']
        assert 'SECRET-KEY' not in json.dumps(result) + caplog.text + str(capsys.readouterr())
    session.assert_not_called()
    spawn.assert_not_called()


@pytest.mark.parametrize('proxy', ['http://localhost:1023', 'socks5://localhost:80',
    'http://user:SECRET-KEY@localhost:7890', 'http://localhost:7890/?key=SECRET-KEY',
    'socks5://remote.example:7890', 'http://localhost:65536'])
async def test_actual_proxy_url_validation_and_secret_redaction(mod, secrets, proxy, caplog, capsys):
    for reader, url in [(mod.read_youtube, YT), (mod.read_instaloader, IG)]:
        result = await reader(url, secrets, {'proxy_url': proxy})
        assert result['reason'] == 'invalid_settings'
        assert 'SECRET-KEY' not in json.dumps(result) + caplog.text + str(capsys.readouterr())


async def test_actual_proxy_network_failure_does_not_echo_credentials(mod, monkeypatch, secrets, caplog, capsys):
    http(monkeypatch, mod, [aiohttp.ClientConnectionError('https://api/?key=SECRET-KEY')])
    result = await mod.read_youtube(YT, secrets, {'proxy_url': 'http://localhost:7890'})
    assert result['reason'] == 'network_error'
    assert 'SECRET-KEY' not in json.dumps(result) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize('provider_reason,reason', [('auth_required', 'access_denied'), ('quota_exceeded', 'rate_limited')])
@pytest.mark.parametrize('partial', [False, True])
def test_provider_failure_actually_pauses_same_source(mod, tmp_path, provider_reason, reason, partial):
    from leadworkbench.db import Store
    store = Store(tmp_path)
    ids = [store.manual_company(name, 'https://example.org')['id'] for name in ['A', 'B']]
    for cid in ids:
        store.add_social(cid, YT, 'known channel')
    run = store.create_social_run(ids)
    while True:
        item = store.claim_social_item()
        assert item is not None
        if item['stage'] == 'read':
            break
        store.finish_social_item(item['id'], {'status': 'succeeded', 'links': []})
    result = mod._empty('youtube', YT)
    if partial:
        result.update(status='preview_read', title='Example', description='Bio')
    result = mod._failure(result, provider_reason)
    assert result['reason'] == reason and result['provider_reason'] == provider_reason
    if partial:
        assert result['status'] == 'preview_read' and result['title'] == 'Example'
    store.finish_social_item(item['id'], result)
    with store.connect() as connection:
        rows = connection.execute("SELECT result FROM social_work WHERE run_id=? AND state='blocked'", (run['id'],)).fetchall()
    assert any(json.loads(row['result']).get('reason') == 'source_paused' for row in rows)


async def test_all_generated_messages_are_chinese(mod, monkeypatch, secrets):
    import re
    assert all(re.search('[\u4e00-\u9fff]', text) for text in mod._MESSAGES.values())
    http(monkeypatch, mod, [Response(channel()), Response(videos())])
    result = await mod.read_youtube(YT, secrets, {})
    assert re.search('[\u4e00-\u9fff]', result['message'])
    fake_instaloader(monkeypatch)
    records = []
    mod._instagram_run(IG, 3, records.append)
    assert all(re.search('[\u4e00-\u9fff]', record['message']) for record in records)
    partial = mod._failure(records[-1], 'network_error')
    assert '帖子' in partial['message'] and 'Profile' not in partial['message']
