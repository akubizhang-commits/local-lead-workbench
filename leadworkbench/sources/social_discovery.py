"""Bounded, unauthenticated source adapters. Never send outreach or reuse cookies."""
import asyncio
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit, urljoin
from urllib.robotparser import RobotFileParser
import aiohttp
from bs4 import BeautifulSoup
from .website import PublicResolver, fetch_page, HTTPError, RobotsDenied, UnsafeURL, USER_AGENT, ROBOTS_AGENT
from .website import FetchError, WebsiteAccessBlocked, PageChallenge, RequestLimit, CLOCK_SKEW_TOLERANCE, _retry_deadline, parse_page
from ..social import organization_social
from ..social_projection import usable_field, social_target as classify_link
from ..website_policy import host_key

PLATFORMS=['instagram','facebook','tiktok','youtube','pinterest','x','bluesky']


def validate_endpoint(value):
    if not value.strip(): return ''
    u=urlsplit(value.strip())
    if (u.scheme!='http' or u.hostname not in {'localhost','127.0.0.1','::1'} or u.username or u.password
            or u.query or u.fragment or u.path not in {'','/','/search'} or not u.port or u.port<1024):
        raise ValueError('SearXNG需使用专用本机服务地址，例如 http://127.0.0.1:18888，端口需大于1023')
    return urlunsplit((u.scheme,u.netloc,'/search','',''))


def parse_search(data):
    if not isinstance(data,dict) or not isinstance(data.get('results'),list):
        raise ValueError('搜索服务未返回SearXNG JSON；请启用JSON输出')
    failures=data.get('unresponsive_engines') or []
    rows=[]
    for r in data['results'][:30]:
        if isinstance(r,dict) and isinstance(r.get('url'),str):
            rows.append({'url':r['url'][:4096],'title':str(r.get('title',''))[:300],
                         'description':str(r.get('content',''))[:1800]})
    status='succeeded' if rows else 'blocked' if failures else 'no_results'
    return {'status':status,'results':rows,'engine_failures':failures,
            'message':'部分搜索引擎未响应' if rows and failures else '搜索引擎未响应，不能判定无结果' if failures else '搜索完成' if rows else '查询成功，未返回结果'}


async def search_searx(endpoint,query):
    endpoint=validate_endpoint(endpoint)
    if not endpoint: return {'status':'blocked','message':'尚未配置本机SearXNG','reason':'not_configured'}
    try:
        async with aiohttp.ClientSession(trust_env=False,cookie_jar=aiohttp.DummyCookieJar()) as session:
            async with session.get(endpoint,params={'q':query,'format':'json','categories':'general'},
                                   allow_redirects=False,timeout=aiohttp.ClientTimeout(total=18)) as response:
                if response.status!=200:
                    result={'status':'blocked','reason':'provider_unavailable','http_status':response.status,
                            'message':f'搜索服务HTTP {response.status}；检查服务及JSON输出权限'}
                    if response.status==429 or response.status>=500:
                        result['not_before'],result['manual_required']=_retry_deadline(
                            response.headers.get('Retry-After'),time.time())
                    return result
                if response.content_length and response.content_length>2_000_000: raise ValueError('搜索响应过大')
                body=bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    body.extend(chunk)
                    if len(body)>2_000_000:raise ValueError('搜索响应过大')
                r=parse_search(json.loads(body))
                if r['status']=='blocked':r['reason']='provider_unavailable'
                return r
    except (aiohttp.ClientError,TimeoutError,OSError,ValueError) as exc:
        return {'status':'blocked','reason':'provider_unavailable','message':f'搜索服务不可用：{type(exc).__name__} {str(exc)[:250]}'}


async def public_html(url, *, access_policy=None):
    """One public page; robots, redirects and wire retries share host pacing."""
    last_request, delays, local_gates = {}, {}, {}

    def check_host(target):
        gates=access_policy.gates() if access_policy is not None else local_gates
        gate=gates.get(host_key(target))
        if gate and (gate.get('manual_required') or gate.get('not_before',0)>time.time()):
            raise WebsiteAccessBlocked(gate)

    def observe(target, exc, *, robots=False):
        if isinstance(exc, WebsiteAccessBlocked):return
        if isinstance(exc,RobotsDenied) and isinstance(exc.__cause__,HTTPError):
            exc=exc.__cause__
        status=exc.status if isinstance(exc, (HTTPError,PageChallenge)) else None
        manual=status in {401,403} or isinstance(exc,(aiohttp.ClientSSLError,PageChallenge))
        transient=status==429 or status is not None and status>=500 or isinstance(exc,(aiohttp.ClientError,OSError,TimeoutError))
        if not (manual or transient or robots):return
        deadline=0
        if transient and not manual:
            deadline,manual=_retry_deadline(getattr(exc,'retry_after',None),time.time())
        else:manual=True
        gate=dict(host=host_key(getattr(exc,'url','') or target),not_before=deadline,
                  manual_required=manual,http_status=status,
                  reason=('robots unavailable: ' if robots else '')+(str(exc) or type(exc).__name__))
        if isinstance(exc,PageChallenge):gate['reason_code']='challenge'
        if access_policy is not None:access_policy.observe(gate)
        local_gates[gate['host']]=gate
        return gate

    async def pace(target):
        host=host_key(target)
        while True:
            check_host(target)
            shared_at,shared_delay=access_policy.timing(target) if access_policy is not None else (0,2)
            previous=max(shared_at,last_request.get(host,0))
            delay=max(2,shared_delay,delays.get(host,0))
            now=time.time()
            if previous>now+CLOCK_SKEW_TOLERANCE:
                gate=dict(host=host,not_before=0,manual_required=True,http_status=None,
                          reason='System clock moved backwards; check the clock before retrying')
                if access_policy is not None:access_policy.observe(gate)
                raise WebsiteAccessBlocked(gate)
            wait=delay-max(0,now-previous)
            if wait<=0:break
            await asyncio.sleep(wait)
        # No await between consulting the shared timestamp and recording this
        # send. Other lanes see the reservation before they can send.
        last_request[host]=now
        delays[host]=delay
        if access_policy is not None:access_policy.record_request(target,now,delay)

    requests=0
    class PacedRequest(aiohttp.ClientRequest):
        async def send(self, connection):
            nonlocal requests
            if requests>=12:raise RequestLimit('公开页面 HTTP 请求超过预算（含 robots、重定向和重发）')
            await pace(str(self.url))
            requests+=1
            return await super().send(connection)

    async def checked(target):
        check_host(target)

    resolver=PublicResolver()
    connector=aiohttp.TCPConnector(resolver=resolver,use_dns_cache=False,force_close=True,limit=1)
    try:
        async with asyncio.timeout(24), aiohttp.ClientSession(connector=connector,request_class=PacedRequest,trust_env=False,
            auto_decompress=False,cookie_jar=aiohttp.DummyCookieJar(),headers={
            'User-Agent':USER_AGENT,'Accept-Encoding':'identity','Accept':'text/html,text/plain;q=0.9'}) as session:
            cache={}
            async def allowed(target):
                check_host(target)
                u=urlsplit(target); origin=f'{u.scheme}://{u.netloc}'
                if origin not in cache:
                    try: r=await fetch_page(session,origin+'/robots.txt',robots=True,max_bytes=262144,before_request=checked)
                    except HTTPError as exc:
                        if exc.status not in {404,410}:
                            observe(origin,exc,robots=True)
                            raise RobotsDenied(f'robots不可读取 HTTP {exc.status}') from exc
                        text=''
                    except (WebsiteAccessBlocked,RequestLimit):
                        raise
                    except FetchError as exc:
                        observe(origin,exc,robots=True)
                        raise
                    except (aiohttp.ClientError,OSError,TimeoutError) as exc:
                        observe(origin,exc,robots=True)
                        raise
                    else:
                        text=r['text']
                        if text.strip() and not re.search(r'(?im)^\s*user-agent\s*:',text) and any(l.strip() and not l.lstrip().startswith('#') for l in text.splitlines()):
                            raise RobotsDenied('robots返回非规则页面')
                    parser=RobotFileParser();parser.parse(text.splitlines());cache[origin]=parser
                parser=cache[origin]
                if not parser.can_fetch(ROBOTS_AGENT,target):raise RobotsDenied('来源robots规则不允许读取此页面')
                delay=parser.crawl_delay(ROBOTS_AGENT) or 0
                rate=parser.request_rate(ROBOTS_AGENT)
                if rate and rate.requests>0: delay=max(delay,rate.seconds/rate.requests)
                if delay>10:raise RobotsDenied('来源要求的读取间隔超过此次任务预算')
                host=host_key(target)
                delays[host]=max(2,delays.get(host,0),delay)
            response=await fetch_page(session,url,max_bytes=3_000_000,allow_url=allowed,before_request=checked)
            # Reuse the website parser's bounded, conservative challenge check.
            # Discard its contact projection: this source only discovers social
            # links or parses the social page's own public profile fields.
            parse_page(response['text'],response['url'])
            return response
    except (HTTPError,RobotsDenied,PageChallenge,aiohttp.ClientError,OSError,TimeoutError) as exc:
        exc.website_access=observe(url,exc,robots=isinstance(exc,RobotsDenied))
        raise
    finally:
        await connector.close()
        await resolver.close()


def page_gate(soup, url):
    """Recognize actual login/verification surfaces, not substrings in handles."""
    path=[part.lower() for part in urlsplit(url).path.split('/') if part]
    challenge=bool(path and path[0] in {'challenge','checkpoint'})
    login=bool(path and path[0] in {'login','login.php'} or
               path[:2]==['accounts','login'] or path[:3]==['i','flow','login'])

    def visible(node):
        for parent in (node, *node.parents):
            if not getattr(parent,'attrs',None):
                continue
            if parent.has_attr('hidden') or parent.get('aria-hidden')=='true':
                return False
            if re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',str(parent.get('style','')),re.I):
                return False
        return True

    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    text=[]
    size=0
    for node in soup.find_all(string=True):
        parents=list(node.parents)
        if any(p.name in {'script','style','template','noscript','article','header','head'}
               or p.get('role')=='article'
               or p.get('data-testid') in {'UserName','UserDescription'}
               for p in parents) or not visible(node.parent):
            continue
        text.append(str(node).strip())
        size+=len(str(node))
        if size>=4000:
            break
    body=' '.join(text)[:4000]
    if challenge or re.search(r'\bcaptcha\b|verify you are human|security check|安全验证',title+' '+body,re.I):
        return {'status':'challenge','reason':'challenge','message':'需要平台验证，已停止读取'}
    headings=[title]+[node.get_text(' ',strip=True) for node in soup.select('main h1, [role="main"] h1, [role="heading"]')
                      if visible(node) and not node.find_parent(['header','article'])]
    login_heading=any(re.search(r'^(?:log\s*in|sign\s*in|登录)(?:\s*(?:[•|–—:-]|$)|\s+to\b)',value,re.I)
                      for value in headings)
    if (login or any(visible(node) for node in soup.select('input[type="password"]'))
            or login_heading
            or re.search(r'\blog\s*in to continue\b',body,re.I)):
        return {'status':'auth_required','reason':'login_required','message':'页面要求登录；没有读取登录后内容'}
    return None


def parse_public_profile(html,url):
    soup=BeautifulSoup(html[:4_000_000],'html.parser')
    if len(soup.find_all(True,limit=20001))>20000: return {'status':'unreadable','message':'页面结构超过解析预算'}
    blocked=page_gate(soup,url)
    if blocked:
        return blocked
    if classify_link(url)['kind'] not in {'account','post'}:
        return {'status':'blocked','reason':'unsupported_url','message':'地址不是可读取的公开账号或帖子'}
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    meta_title=soup.find('meta',attrs={'property':'og:title'})
    description=soup.find('meta',attrs={'property':'og:description'})
    if meta_title:title=str(meta_title.get('content','')).strip()
    desc=str(description.get('content','')).strip() if description else ''
    if not desc or title.strip().lower() in {'instagram','facebook','tiktok','x','pinterest','youtube','bluesky'}:
        return {'status':'unreadable','message':'仅返回平台通用页面或脚本外壳，未读到账号预览'}
    fields=[]
    for email in sorted(set(re.findall(r'(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',desc))):
        if usable_field('email',email):
            fields.append({'key':'email','value':email,'evidence':desc[:1800]})
    for match in re.finditer(r'(?:phone|tel(?:ephone)?|call|whatsapp|电话)\s*[:：]?\s*(\+?[\d(][\d() .-]{6,24}\d)',desc,re.I):
        number=match.group(1).strip()
        if usable_field('phone',number):fields.append({'key':'phone','value':number,'evidence':desc[:1800]})
    return {'status':'preview_read','message':'已读取公开预览；不是完整帖子或已验证人员资料',
            'title':title[:300],'description':desc[:1800],'fields':fields,'source_url':url}


def website_candidates(html,url):
    soup=BeautifulSoup(html,'html.parser');links={}
    if len(soup.find_all(True,limit=20001))>20000:return []
    for a in soup.find_all('a',href=True):
        target=urljoin(url,a['href']);r=classify_link(target)
        if r['kind']=='excluded':continue
        # Use the narrow parent only; a broad footer can contain both Follow us and a designer credit.
        context=a.parent.get_text(' ',strip=True)[:500] if a.parent else a.get_text(' ',strip=True)
        text=a.get_text(' ',strip=True)
        ambiguous=bool(re.search(r'designed by|site by|website by|powered by|our clients|testimonial|网站制作',context,re.I))
        links[r['url']]={'url':r['url'],'evidence':context or text or target,
                         'source_url':url,'method':'website','association_hint':'third_party_possible' if ambiguous else 'website_link'}
    for r in organization_social([s.string or s.get_text() for s in soup.find_all('script',attrs={'type':'application/ld+json'})[:16]],url):
        if classify_link(r['url'])['kind']!='account':continue
        links[r['url']]={**r,'source_url':url,'method':'website','association_hint':'organization_sameAs'}
    return list(links.values())


async def read_public(url,stage,*,access_policy=None):
    entry=classify_link(url)
    if stage in {'read','profile'} and (entry['platform'] not in PLATFORMS or entry['kind'] not in {'account','post'}):
        return {'status':'blocked','reason':'unsupported','message':'本适配器不自动读取该平台，保留原平台入口'}
    def failure(exc,status,reason,message):
        result={'status':status,'reason':reason,'message':message}
        access=getattr(exc,'website_access',None)
        if access:result['website_access']=access
        return result
    try:
        response=await public_html(url,access_policy=access_policy)
        if stage=='website':
            links=website_candidates(response['text'],response['url'])
            return {'status':'succeeded','message':f'官网读取完成，发现{len(links)}个候选链接','links':links,'source_url':response['url']}
        result=parse_public_profile(response['text'],response['url'])
        result['source_url']=response['url']
        return result
    except WebsiteAccessBlocked as exc:
        gate=exc.access
        reason='challenge' if gate.get('reason_code')=='challenge' else 'access_denied' if gate.get('http_status') in {401,403} else 'rate_limited' if gate.get('http_status')==429 else 'network_error' if not gate.get('manual_required') else 'read_error'
        return {'status':'blocked','reason':reason,'message':'此主机读取已暂停：'+gate.get('reason',''),
                'website_access':gate}
    except PageChallenge as exc:
        return failure(exc,'challenge','challenge','来源要求验证，已暂停此主机读取')
    except RobotsDenied as exc: return failure(exc,'blocked','robots_denied',str(exc))
    except HTTPError as exc:
        reason='rate_limited' if exc.status==429 else 'access_denied' if exc.status in {401,403} else 'network_error' if exc.status>=500 else 'http_error'
        return failure(exc,'blocked' if exc.status in {401,403,429} else 'failed',reason,f'来源返回HTTP {exc.status}')
    except UnsafeURL as exc:return {'status':'blocked','reason':'unsafe_redirect','message':str(exc)}
    except (TimeoutError,aiohttp.ClientError,OSError) as exc:
        return failure(exc,'failed','network_error',f'公开页面读取失败：{type(exc).__name__}；未判定账号不存在或必须登录')
    except Exception as exc:return {'status':'failed','reason':'read_error','message':f'{type(exc).__name__}: {str(exc)[:400]}'}
