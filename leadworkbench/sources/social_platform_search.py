"""Bounded in-platform account discovery using the same profile as manual login.

Only visible search-result links become candidates. No password collection or
automatic ownership decisions; empty/unrecognized layouts fail closed.
"""
import asyncio
import re
from urllib.parse import urlencode, urljoin, urlsplit
from bs4 import BeautifulSoup
from ..social_projection import social_target as classify_link
from .social_browser import profile_lock, browser_context, PLATFORMS, ProfileBusy
from .social_discovery import page_gate


def parse_candidates(html, platform, query, source_url):
    soup = BeautifulSoup(html[:4_000_000], 'html.parser')
    blocked = page_gate(soup, source_url)
    if blocked:
        return {**blocked, 'links': [], 'query': query, 'source_url': source_url}
    for element in soup.select('nav, header, [role="navigation"]'):
        element.decompose()
    tokens = re.findall(r'\w+', query.casefold())
    tokens = [t for t in tokens if t not in {'inc', 'llc', 'com', 'the'}]
    links = {}
    for a in soup.select('a[href]'):
        entry = classify_link(urljoin(PLATFORMS[platform], a['href']))
        if entry['kind'] != 'account' or entry['platform'] != platform:
            continue
        label = a.get_text(' ', strip=True) or a.get('aria-label', '')
        match_text = re.sub(r'\W', '', (label + ' ' + entry['label']).casefold())
        if not tokens or not all(t in match_text for t in tokens):
            continue
        links[entry['url']] = {
            'url': entry['url'], 'source_url': source_url,
            'method': 'platform_search',
            'evidence': f'平台站内搜索；查询：{query}；结果：{label[:700]}。名称匹配候选，城市、电话及归属待核实。',
        }
        if len(links) >= 10:
            break
    if links:
        return {'status': 'succeeded', 'links': list(links.values()),
                'query': query, 'source_url': source_url,
                'message': '已找到名称匹配候选，尚未确认归属；未自动抓取候选账号帖子'}
    text = soup.get_text(' ', strip=True)
    empty = re.search(r'no results found|没有找到任何结果|未找到结果|找不到结果', text, re.I)
    return {'status': 'no_results' if empty else 'blocked',
            'reason': 'no_name_match' if empty else 'search_unrecognized',
            'query': query, 'source_url': source_url, 'links': [],
            'message': '平台显示无搜索结果' if empty else '未识别到名称匹配的搜索结果；可能尚未加载或页面结构不支持，不能据此判断企业没有账号'}


async def search_platform(platform, query, directory, settings):
    if platform not in PLATFORMS:
        return {'status': 'blocked', 'reason': 'unsupported', 'message': '暂不支持此平台'}
    try:
        async with asyncio.timeout(60):
            with profile_lock(directory, platform) as path:
                driver, context = await browser_context(path, settings, True)
                try:
                    page = await context.new_page()
                    async def guard(route):
                        from .website import normalize_url
                        try:
                            normalize_url(route.request.url)
                            host = urlsplit(route.request.url).hostname or ''
                            if route.request.is_navigation_request() and route.request.frame == page.main_frame:
                                if host != platform+'.com' and not host.endswith('.'+platform+'.com'):
                                    return await route.abort()
                        except Exception:
                            return await route.abort()
                        await route.continue_()
                    await page.route('**/*', guard)
                    source = (PLATFORMS[platform]+'search/pages/?'+urlencode({'q': query})
                              if platform == 'facebook' else (PLATFORMS[platform]+'search?'+urlencode({'q':query,'f':'user'}) if platform=='x' else PLATFORMS[platform]))
                    response = await page.goto(source, wait_until='domcontentloaded', timeout=30000)
                    if response and response.status >= 400:
                        return {'status': 'blocked', 'reason': 'rate_limited' if response.status == 429 else 'network_error' if response.status >= 500 else 'access_denied',
                                'message': f'站内搜索返回 HTTP {response.status}', 'source_url': source}
                    # Check gates before and after dynamic navigation; do not infer
                    # authentication from having a profile directory.
                    async def gate():
                        return page_gate(BeautifulSoup((await page.content())[:4_000_000], 'html.parser'), page.url)
                    await page.wait_for_timeout(1200)
                    blocked = await gate()
                    if blocked:
                        return blocked
                    if platform == 'instagram':
                        # Activate search, then type into the platform's own box.
                        trigger = page.get_by_role('link', name=re.compile(r'^(Search|搜索)$', re.I)).first
                        if not await trigger.count():
                            trigger = page.get_by_role('button', name=re.compile(r'^(Search|搜索)$', re.I)).first
                        try:
                            await trigger.click(timeout=10000)
                            search = page.get_by_placeholder(re.compile(r'Search|搜索', re.I)).first
                            await search.fill(query, timeout=8000)
                        except Exception:
                            return await gate() or {'status':'blocked','reason':'search_unrecognized',
                                'message':'未找到可操作的 Instagram 搜索入口；需检查专用会话或页面结构'}
                        # Instagram's search drawer may keep the homepage URL.
                        # Preserve the actual URL; the query lives in evidence.
                        source = page.url
                    result = None
                    for _ in range(6):
                        blocked = await gate()
                        if blocked:
                            return blocked
                        result = parse_candidates(await page.content(), platform, query, source)
                        if result['status'] in {'succeeded', 'no_results', 'auth_required', 'challenge'}:
                            break
                        await page.wait_for_timeout(1000)
                    return result
                finally:
                    try:
                        await context.close()
                    finally:
                        await driver.stop()
    except ProfileBusy:
        return {'status':'blocked','reason':'session_busy','message':'专用浏览器仍在使用，请先关闭并释放会话'}
    except ImportError:
        return {'status':'blocked','reason':'missing_dependency','message':'浏览器依赖未安装'}
    except Exception as exc:
        return {'status':'failed','reason':'network_error','message':f'站内搜索未完成：{type(exc).__name__}；未判定账号不存在'}
