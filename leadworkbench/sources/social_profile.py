"""Extract account-owned profile regions, never feed or comment contacts."""
import re
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup
from ..social_projection import usable_field, social_target as classify_link
from .social_discovery import parse_public_profile


def profile_match(company, fields):
    """Explain exact observed matches; never promote ownership automatically."""
    def host(value):
        try:return (urlsplit(value if '://' in value else '//'+value).hostname or '').lower().removeprefix('www.')
        except ValueError:return ''
    signals=[]
    for field in fields:
        key,value=field['key'],field['value']
        if not usable_field(key,value):continue
        if key in {'website','website_text'} and host(company.get('website','')) and host(value)==host(company['website']):
            signals.append({'text':'关联网站域名与企业官网一致','source_url':field.get('source_url','')})
        if key=='phone' and company.get('phone'):
            digits=re.sub(r'\D','',value)
            if len(digits)>=7 and digits==re.sub(r'\D','',company['phone']):
                signals.append({'text':'公开电话与企业电话一致','source_url':field.get('source_url','')})
    return signals


def parse_profile(html, url):
    soup = BeautifulSoup(html[:4_000_000], 'html.parser')
    base = parse_public_profile(html, url)
    if base['status'] in {'auth_required','challenge','blocked'}:
        return {**base,'posts':[],'provider':'browser','source_url':url,'profile_only':True}
    platform = classify_link(url)['platform']
    fields = []
    def add(key, value, evidence=''):
        value = str(value or '').strip()[:1800]
        if usable_field(key,value) and not any(f['key']==key and f['value']==value for f in fields):
            fields.append({'key':key,'value':value,'evidence':(evidence or value)[:1800],'source_url':url})
    def text(selector):
        node=soup.select_one(selector)
        return node.get_text(' ',strip=True) if node else ''
    name = ''
    bio = ''
    if platform == 'x':
        name=text('[data-testid="UserName"]')
        bio=text('[data-testid="UserDescription"]')
        add('location',text('[data-testid="UserLocation"]'))
        add('joined_at',text('[data-testid="UserJoinDate"]'))
        link=soup.select_one('[data-testid="UserUrl"]')
        if link:
            add('website',link.get('href'),link.get_text(' ',strip=True))
            add('website_text',link.get_text(' ',strip=True))
        for a in soup.select('a[href$="/verified_followers"], a[href$="/followers"], a[href$="/following"]'):
            add('following' if a['href'].endswith('/following') else 'followers',a.get_text(' ',strip=True))
    elif platform == 'instagram':
        header=soup.select_one('main header')
        if header:
            heading=header.select_one('h1,h2')
            name=heading.get_text(' ',strip=True) if heading else ''
            # Only profile header, never article/comment or recommendation text.
            lines=header.get_text('\n',strip=True).splitlines()
            # Counters are rendered as adjacent value/label text nodes.
            end=0
            for i,line in enumerate(lines):
                key={'帖子':'post_count','posts':'post_count','粉丝':'followers','followers':'followers',
                     '关注':'following','following':'following'}.get(line.lower())
                if key and i and re.fullmatch(r'[\d.,万亿KMBkmb ]+',lines[i-1]):
                    add(key,lines[i-1],lines[i-1]+' '+line)
                    end=i+1
            if end:
                first_count=next((i for i,v in enumerate(lines) if re.fullmatch(r'[\d.,万亿KMBkmb ]+',v)),end)
                names=[v for v in lines[:first_count] if v not in {name,'选项','Options'}]
                if names:name=names[-1]
                bio_lines=[]
                for line in lines[end:]:
                    if line in {'关注','发消息','类似账户','Follow','Message','Similar accounts'}:break
                    if line in {'更多','more','...','链接图标'}:continue
                    if re.fullmatch(r'(?:www\.)?[\w.-]+\.[A-Za-z]{2,}(?:/[^\s]*)?',line):
                        add('website_text',line)
                    else:bio_lines.append(line)
                bio='\n'.join(bio_lines)[:1800]
            else:
                bio=''  # Unknown layout: fall back to metadata, never whole header.
            for a in header.select('a[href]'):
                target=urljoin(url,a['href'])
                host=urlsplit(target).hostname or ''
                if host and not (host=='instagram.com' or host.endswith('.instagram.com')):
                    add('website',target,a.get_text(' ',strip=True))
    elif platform == 'facebook':
        name=text('main h1, [role="main"] h1')
        # Intro cards are bounded by a heading and must not include feed articles.
        for heading in soup.select('h2,h3,[role="heading"]'):
            if heading.get_text(' ',strip=True) not in {'Intro','简介','About','关于'}:continue
            node=heading.parent
            for _ in range(4):
                if not node or node.select_one('article,[role="article"]'):break
                value=node.get_text('\n',strip=True)
                if 30<len(value)<1800:
                    bio=value
                node=node.parent
            if bio:break
    desc=bio or base.get('description','')
    name=name or base.get('title','')
    # Safe fallback: contact values are only from the profile region / OG bio.
    for email in re.findall(r'[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',desc):
        add('email',email,desc)
    for match in re.finditer(r'(?:phone|tel|call|whatsapp|电话)\s*[:：]?\s*(\+?[\d(][\d() .-]{6,24}\d)',desc,re.I):
        add('phone',match[1],desc)
    for f in base.get('fields',[]):add(f['key'],f['value'],f.get('evidence',''))
    return {'status':'preview_read' if name or desc or fields else 'unreadable',
            'title':name,'description':desc,'fields':fields,'source_url':url,'provider':'browser','posts':[],
            'profile_only':True,
            'message':'已读取实际呈现的账号基础资料；缺失字段未推测，账号归属待核对' if name or desc or fields else '账号资料尚未加载或当前结构无法识别；未判定账号不存在'}
