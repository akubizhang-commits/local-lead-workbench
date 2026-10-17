"""Conservative URL classification; no network or inferred account ownership."""
import re
from urllib.parse import urlsplit, parse_qs, urlencode

HOSTS = {
    'instagram.com': 'instagram', 'www.instagram.com': 'instagram',
    'facebook.com': 'facebook', 'www.facebook.com': 'facebook', 'm.facebook.com': 'facebook',
    'linkedin.com': 'linkedin', 'www.linkedin.com': 'linkedin',
    'youtube.com': 'youtube', 'www.youtube.com': 'youtube', 'm.youtube.com': 'youtube', 'youtu.be': 'youtube',
    'tiktok.com': 'tiktok', 'www.tiktok.com': 'tiktok',
    'twitter.com': 'x', 'www.twitter.com': 'x', 'x.com': 'x', 'www.x.com': 'x',
    'pinterest.com': 'pinterest', 'www.pinterest.com': 'pinterest', 'au.pinterest.com': 'pinterest',
    'bsky.app': 'bluesky',
}
RESERVED = {'help','about','privacy','legal','terms','accounts','login','logout','signup','search','explore',
            'share','sharer','sharer.php','intent','home','i','settings','watch','feed','results','hashtag',
            'business','developers','policies','reel','reels','p','stories','directory','jobs','notifications'}


def classify_link(value):
    result = {'platform': '', 'kind': 'excluded', 'url': '', 'label': '', 'reason': '不支持的链接或页面类型'}
    try:
        u = urlsplit(value.strip())
        if u.scheme not in {'http', 'https'} or u.username or u.password or u.port not in (None, 80, 443):
            return result
        platform = HOSTS.get((u.hostname or '').lower())
        if not platform:
            return result
        result['platform'] = platform
        parts = [p for p in u.path.split('/') if p]
        # Encoded separators and exotic paths require a future platform adapter.
        if not parts or any(not re.fullmatch(r'[\w.@-]+', p, re.ASCII) for p in parts):
            return result
        p = parts[0].lower(); q = parse_qs(u.query)
        base = {'x':'https://x.com', 'bluesky':'https://bsky.app'}.get(platform, f'https://www.{platform}.com')
        kind = None; path = ''; label = ''
        if platform == 'instagram':
            if p in {'p','reel','tv'} and len(parts)==2:
                kind, path = 'post', f'/{p}/{parts[1]}/'
            elif len(parts)==1 and p not in RESERVED and p not in {'tv','direct','challenge'}:
                kind, path, label = 'account', f'/{p}/', '@'+p
        elif platform == 'facebook':
            if p == 'profile.php' and len(parts)==1 and q.get('id',[''])[0].isdigit():
                kind, path = 'account', '/profile.php?'+urlencode({'id':q['id'][0]})
                label = 'Page '+q['id'][0]
            elif p in {'permalink.php','story.php'} and q.get('story_fbid') and q.get('id'):
                kind, path = 'post', '/'+p+'?'+urlencode({k:q[k][0] for k in ('story_fbid','id')})
            elif p=='watch' and q.get('v'):
                kind, path = 'post', '/watch/?'+urlencode({'v':q['v'][0]})
            elif len(parts)==3 and parts[1].lower() in {'posts','videos'}:
                kind, path = 'post', f'/{p}/{parts[1].lower()}/{parts[2]}/'
            elif p=='reel' and len(parts)==2:
                kind, path = 'post', f'/reel/{parts[1]}/'
            elif len(parts)==1 and p not in RESERVED and p not in {'groups','events','photo.php','photos','marketplace'}:
                kind, path, label = 'account', f'/{p}/', parts[0]
        elif platform == 'linkedin':
            if p in {'company','in','school'} and len(parts)==2:
                kind, path, label = 'account', f'/{p}/{parts[1].lower()}/', parts[1]
            elif p=='posts' and len(parts)==2 or p=='feed' and len(parts)==3 and parts[1]=='update':
                kind, path = 'post', '/'+ '/'.join(parts)+'/'
        elif platform == 'youtube':
            if u.hostname=='youtu.be' and len(parts)==1:
                kind, path = 'post', '/watch?'+urlencode({'v':parts[0]})
            elif p=='watch' and q.get('v'):
                kind, path = 'post', '/watch?'+urlencode({'v':q['v'][0]})
            elif p in {'shorts','live'} and len(parts)==2:
                kind, path = 'post', '/watch?'+urlencode({'v':parts[1]})
            elif p.startswith('@') and len(parts)==1:
                kind, path, label = 'account', '/'+p, p
            elif p in {'channel','c','user'} and len(parts)==2:
                kind, path, label = 'account', '/'+p+'/'+parts[1], parts[1]
        elif platform == 'tiktok':
            if p.startswith('@') and len(parts)==1:
                kind, path, label = 'account', '/'+p, p
            elif p.startswith('@') and len(parts)==3 and parts[1]=='video':
                kind, path = 'post', f'/{p}/video/{parts[2]}'
        elif platform == 'x':
            if len(parts)==1 and p not in RESERVED and re.fullmatch(r'[a-z0-9_]{1,15}',p):
                kind, path, label = 'account', '/'+p, '@'+p
            elif len(parts)==3 and parts[1]=='status' and parts[2].isdigit():
                kind, path = 'post', f'/{p}/status/{parts[2]}'
        elif platform == 'pinterest':
            if p=='pin' and len(parts)==2:
                kind, path = 'post', '/pin/'+parts[1]+'/'
            elif len(parts)==1 and p not in RESERVED and p not in {'pin','ideas','today'}:
                kind, path, label = 'account', '/'+p+'/', parts[0]
            elif len(parts)==2 and p not in RESERVED and p not in {'pin','ideas'}:
                kind, path, label = 'collection', '/'+p+'/'+parts[1]+'/', parts[1]
        elif platform == 'bluesky' and p=='profile':
            if len(parts)==2:
                kind, path, label = 'account', '/profile/'+parts[1].lower(), parts[1]
            elif len(parts)==4 and parts[2]=='post':
                kind, path = 'post', '/profile/'+parts[1].lower()+'/post/'+parts[3]
        if kind:
            return dict(platform=platform, kind=kind, url=base+path, label=label or ('帖子' if kind=='post' else '合集'), reason='')
        return result
    except (ValueError, AttributeError):
        return result


def search_queries(company):
    def quoted(value):
        return '"'+value.replace('"','')+'"'
    queries=[]
    domain = urlsplit(company.get('website') or '').hostname or ''
    terms = [quoted(domain)] if domain else []
    if company.get('name'):
        terms.append(' '.join(quoted(company[k]) for k in ('name','city') if company.get(k)))
    if company.get('phone'):
        terms.append(quoted(company['phone']))
        digits=re.sub(r'\D','',company['phone'])
        if 7 <= len(digits) <= 15:
            terms.append(quoted(digits))
    for platform,site in [('Instagram','instagram.com'),('Facebook','facebook.com'),('LinkedIn','linkedin.com/company'),
                          ('TikTok','tiktok.com'),('YouTube','youtube.com')]:
        for term in dict.fromkeys(terms):
            query = f'{term} site:{site}'
            queries.append({'platform':platform,'query':query,'url':'https://www.google.com/search?'+urlencode({'q':query})})
    return queries


def organization_social(scripts, source_url):
    """Only explicit sameAs for an organization that declares this site's URL."""
    import json
    host=(urlsplit(source_url).hostname or '').removeprefix('www.')
    budget=1_000_000; visited=0; results=[]
    for raw in scripts[:16]:
        size=len(raw.encode('utf-8'))
        if size>budget: continue
        budget-=size
        try: payload=json.loads(raw)
        except (ValueError,RecursionError): continue
        pending=[(payload,0)]
        while pending and visited<1000:
            node,depth=pending.pop(); visited+=1
            if depth>12: continue
            if isinstance(node,list):
                pending.extend((v,depth+1) for v in node[:1000])
            elif isinstance(node,dict):
                types=node.get('@type',[])
                if isinstance(types,str): types=[types]
                org_url=node.get('url','')
                if isinstance(types,list) and isinstance(org_url,str):
                    try: own=(urlsplit(org_url).hostname or '').removeprefix('www.')==host
                    except ValueError: own=False
                    if own and any(t in {'Organization','LocalBusiness','Store','ClothingStore','Corporation'} for t in types if isinstance(t,str)):
                        urls=node.get('sameAs',[])
                        if isinstance(urls,str): urls=[urls]
                        if isinstance(urls,list):
                            for url in urls[:100]:
                                r=classify_link(url)
                                if r['kind']=='account':
                                    results.append({'url':r['url'],'evidence':json.dumps({'@type':types,'url':org_url,'sameAs':url},ensure_ascii=False)})
                pending.extend((v,depth+1) for k,v in node.items() if k not in {'author','creator','review','sameAs'})
    return results
