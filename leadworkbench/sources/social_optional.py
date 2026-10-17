"""Opt-in, bounded public metadata readers; no login, sessions or media downloads.

Settings: max_posts (default 3, clamped 0..5), proxy_url (local HTTP URL).
Legacy proxy accepts a URL or {server: URL}; proxy_url always takes precedence.
Local SOCKS5 settings are recognized but blocked with HTTP proxy guidance.
YouTube credentials belong only in social-secrets.json.
The private CLI streams complete snapshots so a killed child loses no preview.
"""
import asyncio
import contextlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import re
import sys
from datetime import timezone
from urllib.parse import urlsplit

import aiohttp

INSTALOADER_TIMEOUT = 60
TERMINATE_GRACE = 2
YOUTUBE_TIMEOUT = 30
MAX_RESPONSE = 1_000_000
MAX_CHILD_OUTPUT = 512_000
CHANNEL_ID = r'UC[A-Za-z0-9_-]{22}'

_MESSAGES = {
    'unsupported_url': '不支持此链接；请使用规范的公开账号主页地址。',
    'invalid_settings': '帖子数量或本机代理配置无效；代理仅限 localhost、127.0.0.1 或 ::1，端口须为 1024–65535，不能包含账号密码。',
    'unsupported_proxy': '此可选采集器不支持 SOCKS5 代理；请改用本机代理服务的 HTTP 代理地址和端口。',
    'dependency_missing': '缺少可选依赖 Instaloader；请安装 instaloader 后启用此采集器。',
    'not_configured': '请在 social-secrets.json 中配置 youtube_api_key。',
    'auth_required': '来源要求认证或 API 凭据被拒绝；未尝试登录。',
    'access_denied': '来源拒绝访问；不能据此判定必须登录。',
    'quota_exceeded': 'YouTube API 配额已用尽，已停止读取。',
    'rate_limited': '来源限制请求频率，已停止读取。',
    'network_error': '来源网络连接失败或请求超时。',
    'not_found': '来源未返回匹配的公开资源。',
    'response_too_large': '来源响应超过大小限制，已停止读取。',
    'invalid_response': '来源返回的数据无效或不完整。',
    'provider_error': '采集器未能读取公开资料。',
    'unsafe_redirect': '来源重定向已被阻止。',
}
_BLOCKED = {'unsupported_url', 'invalid_settings', 'dependency_missing', 'not_configured',
            'auth_required', 'access_denied', 'quota_exceeded', 'rate_limited', 'unsafe_redirect',
            'unsupported_proxy'}
_STOP_REASONS = {'access_denied', 'auth_required', 'login_required', 'challenge',
                 'rate_limited', 'robots_denied', 'unsafe_redirect'}


class ProviderError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _empty(provider, url):
    return dict(status='failed', title='', description='', fields=[], source_url=url,
                posts=[], provider=provider, message='')


def _failure(result, reason):
    # Store source-pausing uses these broad reasons even for partial previews.
    # Keep the precise provider failure without losing the successful profile.
    pause_reason = {'auth_required': 'access_denied', 'quota_exceeded': 'rate_limited'}.get(reason, reason)
    # A child/transport failure after a restricted preview does not grant a
    # new attempt. Preserve the strongest observed stop and the collected data.
    if result.get('reason') in _STOP_REASONS and pause_reason not in {'access_denied', 'auth_required', 'login_required', 'challenge'}:
        return dict(result)
    result = {**result, 'reason': pause_reason, 'message': _MESSAGES[reason]}
    if pause_reason != reason:
        result['provider_reason'] = reason
    if result['status'] == 'preview_read':
        result.update(partial=True, message='已读取账号资料，但帖子读取不完整。' + _MESSAGES[reason])
    else:
        result['status'] = 'blocked' if reason in _BLOCKED else 'failed'
    return result


def _text(value, limit):
    if not isinstance(value, str):
        raise ProviderError('invalid_response')
    return value[:limit]


def _target(url, provider):
    # Whole-string matching rejects credentials, ports, queries, fragments,
    # escaped separators, whitespace, content links and platform aliases.
    if not isinstance(url, str) or len(url) > 2048:
        raise ProviderError('unsupported_url')
    if provider == 'instaloader':
        match = re.fullmatch(r'https://(?:www\.)?instagram\.com/([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)/?', url)
        if match:
            name = match[1]
            reserved = {'accounts', 'about', 'explore', 'direct', 'challenge', 'p', 'reel', 'reels',
                        'tv', 'stories', 'developer', 'developers', 'legal', 'privacy', 'terms',
                        'help', 'login', 'logout', 'signup', 'search', 'settings', 'web', 'api'}
            if name.lower() not in reserved and '..' not in name:
                return name
    else:
        match = re.fullmatch(r'https://(?:www\.)?youtube\.com/(@[\w.-]{1,100}|channel/' + CHANNEL_ID + r'|user/[A-Za-z0-9_-]{1,100})/?', url)
        if match:
            path = match[1]
            if path.startswith('@'):
                return {'forHandle': path}
            kind, value = path.split('/')
            return {'id' if kind == 'channel' else 'forUsername': value}
    raise ProviderError('unsupported_url')


def _options(settings):
    if not isinstance(settings, dict):
        raise ProviderError('invalid_settings')
    count = settings.get('max_posts', 3)
    if isinstance(count, bool) or not isinstance(count, int):
        raise ProviderError('invalid_settings')
    proxy = settings['proxy_url'] if 'proxy_url' in settings else settings.get('proxy', '')
    if 'proxy_url' not in settings and isinstance(proxy, dict):
        if any(k not in {'server'} for k in proxy):
            raise ProviderError('invalid_settings')
        proxy = proxy.get('server') or ''
    if not isinstance(proxy, str):
        raise ProviderError('invalid_settings')
    if proxy:
        try:
            u = urlsplit(proxy)
            if (proxy != proxy.strip() or u.scheme not in {'http', 'socks5'} or
                    u.hostname not in {'localhost', '127.0.0.1', '::1'} or
                    u.username is not None or u.password is not None or
                    not u.port or u.port < 1024 or u.path not in {'', '/'} or u.query or u.fragment):
                raise ValueError
        except ValueError:
            raise ProviderError('invalid_settings') from None
        if u.scheme == 'socks5':
            raise ProviderError('unsupported_proxy')
    return max(0, min(count, 5)), proxy or None


def _api_reason(status, data):
    error = data.get('error', {}) if isinstance(data, dict) else {}
    error = error if isinstance(error, dict) else {}
    rows = error.get('errors', [])
    reasons = {r.get('reason') for r in rows if isinstance(r, dict) and isinstance(r.get('reason'), str)} if isinstance(rows, list) else set()
    details = error.get('details', [])
    if isinstance(details, list):
        reasons.update(d['reason'] for d in details if isinstance(d, dict) and isinstance(d.get('reason'), str))
    if reasons & {'quotaExceeded', 'dailyLimitExceeded', 'dailyLimitExceededUnreg'}:
        return 'quota_exceeded'
    if status == 429 or reasons & {'rateLimitExceeded', 'userRateLimitExceeded'}:
        return 'rate_limited'
    if status == 401 or reasons & {'keyInvalid', 'authError', 'required', 'API_KEY_INVALID', 'API_KEY_EXPIRED'}:
        return 'auth_required'
    if status == 403:
        return 'access_denied'
    if status == 404:
        return 'not_found'
    if status >= 500:
        return 'network_error'
    if 300 <= status < 400:
        return 'unsafe_redirect'
    return 'provider_error'


async def _youtube_get(session, endpoint, params, proxy):
    async with session.get('https://www.googleapis.com/youtube/v3/' + endpoint,
                           params=params, proxy=proxy, allow_redirects=False,
                           timeout=aiohttp.ClientTimeout(total=12, connect=5)) as response:
        if response.content_length and response.content_length > MAX_RESPONSE:
            raise ProviderError('response_too_large')
        body = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE:
                raise ProviderError('response_too_large')
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            if response.status != 200:
                raise ProviderError(_api_reason(response.status, {})) from None
            raise ProviderError('invalid_response') from None
        if response.status != 200:
            raise ProviderError(_api_reason(response.status, data))
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ProviderError('invalid_response')
        return data['items']


def _youtube_key(directory):
    try:
        with (Path(directory) / 'social-secrets.json').open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError
        data = json.loads(raw)
        key = data.get('youtube_api_key') if isinstance(data, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', key):
            raise ValueError
        return key
    except (OSError, ValueError, TypeError, RecursionError):
        raise ProviderError('not_configured') from None


async def read_youtube(url, directory, settings):
    """Read a public channel and one bounded uploads page using the official API."""
    result = _empty('youtube', url)
    try:
        selector = _target(url, 'youtube')
        count, proxy = _options(settings)
        key = _youtube_key(directory)
        async with asyncio.timeout(YOUTUBE_TIMEOUT):
            async with aiohttp.ClientSession(trust_env=False, cookie_jar=aiohttp.DummyCookieJar()) as session:
                rows = await _youtube_get(session, 'channels',
                    dict(part='snippet,contentDetails', maxResults=1, key=key, **selector), proxy)
                if not rows:
                    raise ProviderError('not_found')
                row = rows[0]
                if not isinstance(row, dict) or not re.fullmatch(CHANNEL_ID, str(row.get('id', ''))):
                    raise ProviderError('invalid_response')
                snippet = row['snippet']
                title = _text(snippet['title'], 300)
                description = _text(snippet.get('description', ''), 1800)
                result.update(status='preview_read', title=title, description=description,
                              message='已读取公开频道预览。')
                if count:
                    uploads = row['contentDetails']['relatedPlaylists']['uploads']
                    if not isinstance(uploads, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', uploads):
                        raise ProviderError('invalid_response')
                    posts = await _youtube_get(session, 'playlistItems',
                        dict(part='snippet,contentDetails', playlistId=uploads, maxResults=count, key=key), proxy)
                    for item in posts[:count]:
                        snip = item['snippet']
                        video = snip['resourceId']['videoId']
                        if not isinstance(video, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
                            raise ProviderError('invalid_response')
                        # Playlist insertion time is not necessarily publication time.
                        published = item.get('contentDetails', {}).get('videoPublishedAt', '')
                        result['posts'].append(dict(url='https://www.youtube.com/watch?v=' + video,
                            text='\n'.join(filter(None, [_text(snip.get('title', ''), 300),
                                                       _text(snip.get('description', ''), 1800)])),
                            published_at=_text(published, 64)))
        return result
    except ProviderError as exc:
        return _failure(result, exc.reason)
    except (aiohttp.ClientError, TimeoutError, OSError):
        return _failure(result, 'network_error')
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return _failure(result, 'invalid_response')


def _instagram_reason(exc):
    if isinstance(exc, ProviderError):
        return exc.reason
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & {'LoginRequiredException', 'PrivateProfileNotFollowedException', 'LoginException',
                'TwoFactorAuthRequiredException', 'BadCredentialsException'}:
        return 'auth_required'
    if 'TooManyRequestsException' in names:
        return 'rate_limited'
    if 'QueryReturnedForbiddenException' in names:
        return 'access_denied'
    if names & {'ProfileNotExistsException', 'QueryReturnedNotFoundException'}:
        return 'not_found'
    if names & {'BadResponseException', 'JSONDecodeError', 'KeyError', 'TypeError', 'ValueError'}:
        return 'invalid_response'
    if names & {'ConnectionException', 'ConnectionError', 'Timeout', 'TimeoutError', 'RequestException'}:
        return 'network_error'
    return 'provider_error'


def _instagram_run(url, count, emit):
    """Child-only synchronous work. Emit profile before touching the post iterator."""
    result = _empty('instaloader', url)
    loader = None
    try:
        import instaloader
        name = _target(url, 'instaloader')
        loader = instaloader.Instaloader(quiet=True, download_pictures=False, download_videos=False,
            download_video_thumbnails=False, download_comments=False, save_metadata=False,
            post_metadata_txt_pattern='', max_connection_attempts=1, request_timeout=12,
            iphone_support=False)
        profile = instaloader.Profile.from_username(loader.context, name)
        if profile.is_private:
            raise ProviderError('auth_required')
        title = _text(profile.full_name, 300)
        description = _text(profile.biography, 1800)
        result.update(status='preview_read', title=title, description=description,
                      message='已读取公开账号预览。')
        emit({**result, 'posts': list(result['posts'])})
        if count:
            for post in itertools.islice(profile.get_posts(), min(count, 5)):
                code = post.shortcode
                if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', code):
                    raise ProviderError('invalid_response')
                published = post.date_utc
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                result['posts'].append(dict(url='https://www.instagram.com/p/' + code + '/',
                    text=_text(post.caption or '', 1800), published_at=published.isoformat()))
                emit({**result, 'posts': list(result['posts'])})
    except ImportError:
        emit(_failure(result, 'dependency_missing'))
    except Exception as exc:
        emit(_failure(result, _instagram_reason(exc)))
    finally:
        if loader is not None:
            with contextlib.suppress(Exception):
                loader.close()


async def _reap(child):
    async def discard_output():
        while await child.stdout.read(16384):
            pass

    # A full StreamReader pauses its pipe transport. Drain without retaining data
    # while waiting, otherwise even a killed process's wait() can remain pending.
    drain = asyncio.create_task(discard_output())
    try:
        if child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                child.terminate()
            try:
                await asyncio.wait_for(child.wait(), TERMINATE_GRACE)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    child.kill()
                await child.wait()
        else:
            await child.wait()
        await drain
    finally:
        if not drain.done():
            drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain


async def read_instaloader(url, directory, settings):
    """Run the optional library in an isolated, noninteractive, 60-second child."""
    result = _empty('instaloader', url)
    child = None
    try:
        _target(url, 'instaloader')
        count, proxy = _options(settings)
        if importlib.util.find_spec('instaloader') is None:
            raise ProviderError('dependency_missing')
        # No inherited credentials, proxy variables, Python startup hooks or netrc.
        env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'LANG') if k in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', NETRC=os.devnull)
        async with asyncio.timeout(INSTALOADER_TIMEOUT):
            child = await asyncio.create_subprocess_exec(sys.executable, '-B', str(Path(__file__).resolve()),
                '--instagram-child', url, str(count), proxy or '', stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=env, limit=MAX_CHILD_OUTPUT)
            size = 0
            received = False
            while True:
                try:
                    line = await child.stdout.readline()
                except ValueError:
                    raise ProviderError('response_too_large') from None
                if not line:
                    break
                size += len(line)
                if size > MAX_CHILD_OUTPUT:
                    raise ProviderError('response_too_large')
                record = json.loads(line)
                if (not isinstance(record, dict) or record.get('provider') != 'instaloader' or
                        record.get('source_url') != url or
                        not all(k in record for k in ('status', 'title', 'description', 'fields', 'posts', 'message')) or
                        record['status'] not in {'preview_read', 'failed', 'blocked'} or
                        not all(isinstance(record[k], str) for k in ('title', 'description', 'message')) or
                        not isinstance(record['fields'], list) or
                        not all(isinstance(f, dict) and isinstance(f.get('key'), str) and
                                isinstance(f.get('value'), str) for f in record['fields']) or
                        not isinstance(record['posts'], list) or len(record['posts']) > count or
                        not all(isinstance(p, dict) and isinstance(p.get('url'), str) and
                                isinstance(p.get('text'), str) for p in record['posts'])):
                    raise ProviderError('invalid_response')
                if result['status'] == 'preview_read' and record['status'] != 'preview_read':
                    reason = record.get('provider_reason') or record.get('reason')
                    result = _failure(result, reason if reason in _MESSAGES else 'invalid_response')
                else:
                    result = record
                received = True
                if result.get('reason') in _STOP_REASONS:
                    return result
            code = await child.wait()
            if code or not received:
                raise ProviderError('provider_error')
        return result
    except ProviderError as exc:
        return _failure(result, exc.reason)
    except (TimeoutError, OSError):
        return _failure(result, 'network_error')
    except (ValueError, TypeError, RecursionError):
        return _failure(result, 'invalid_response')
    finally:
        if child is not None:
            # Shield cleanup even if the caller cancels while we are reaping.
            cleanup = asyncio.create_task(_reap(child))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


@contextlib.contextmanager
def _bounded_instagram_transport(proxy):
    """Child-local requests guard, including sessions created internally by Instaloader."""
    import requests
    original = requests.sessions.Session.send

    def bounded_send(session, request, **kwargs):
        target = urlsplit(request.url)
        if target.scheme != 'https' or target.hostname not in {'www.instagram.com', 'i.instagram.com', 'instagram.com'}:
            raise ProviderError('unsafe_redirect')
        kwargs.update(stream=True, allow_redirects=False, timeout=(5, 12),
                      proxies={'http': proxy, 'https': proxy} if proxy else {})
        response = original(session, request, **kwargs)
        try:
            status = response.status_code
            if status == 401:
                raise ProviderError('auth_required')
            if status == 403:
                raise ProviderError('access_denied')
            if status == 429:
                raise ProviderError('rate_limited')
            if status >= 500:
                raise ProviderError('network_error')
            if 300 <= status < 400:
                location = response.headers.get('Location', '')
                raise ProviderError('auth_required' if '/accounts/login' in location else 'unsafe_redirect')
            body = bytearray()
            for chunk in response.iter_content(16384):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE:
                    raise ProviderError('response_too_large')
            response._content = bytes(body)
            response._content_consumed = True
            return response
        finally:
            response.close()

    requests.sessions.Session.send = bounded_send
    try:
        yield
    finally:
        requests.sessions.Session.send = original


def _main():
    if len(sys.argv) != 5 or sys.argv[1] != '--instagram-child':
        return 2
    url, raw_count, proxy = sys.argv[2:]
    # Keep library prints/logs out of the structured pipe; never collect stderr.
    output = sys.stdout
    last = _empty('instaloader', url)
    def emit(value):
        nonlocal last
        last = value
        output.write(json.dumps(value, ensure_ascii=True) + '\n')
        output.flush()
    try:
        _target(url, 'instaloader')
        count, proxy = _options({'max_posts': int(raw_count), 'proxy_url': proxy})
        with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            with _bounded_instagram_transport(proxy):
                _instagram_run(url, count, emit)
    except ImportError:
        emit(_failure(last, 'dependency_missing'))
    except Exception as exc:
        emit(_failure(last, _instagram_reason(exc)))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
