"""Dedicated browser profiles for user-controlled login and bounded social reads."""
import asyncio
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from ..social_projection import social_target as classify_link
from .social_discovery import parse_public_profile, page_gate

PLATFORMS={'instagram':'https://www.instagram.com/','facebook':'https://www.facebook.com/','x':'https://x.com/'}
SESSIONS={}

class ProfileBusy(RuntimeError):
 """Only an actual profile lock conflict, not a browser/runtime failure."""

def profile_path(directory,platform):
 if platform not in PLATFORMS:raise ValueError('仅支持 Instagram、Facebook 和 Twitter（X）浏览器会话')
 root=Path(directory)/'social-browser';root.mkdir(parents=True,exist_ok=True);os.chmod(root,0o700)
 p=root/platform;p.mkdir(exist_ok=True);os.chmod(p,0o700)
 return p

@contextmanager
def profile_lock(directory,platform):
 p=profile_path(directory,platform)
 with (p.parent/(platform+'.lock')).open('a') as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ProfileBusy('专用浏览器正在使用，请先关闭登录窗口再采集')
  try:yield p
  finally:fcntl.flock(f,fcntl.LOCK_UN)

def session_status(directory,platform):
 p=SESSIONS.get((str(directory),platform))
 return {'state':'open' if p and p.poll() is None else 'closed','message':'窗口打开不代表已登录；关闭后采集可复用该专用配置目录'}

def open_session(directory,platform):
 import importlib.util
 if platform not in PLATFORMS:raise ValueError('不支持的平台')
 if not importlib.util.find_spec('playwright'):raise ValueError('尚未安装浏览器依赖，请运行 scripts/install_social.sh')
 key=(str(directory),platform)
 if session_status(directory,platform)['state']=='open':return {'status':'open','message':'登录窗口已经打开'}
 with profile_lock(directory,platform):pass
 log=Path(directory)/'social-browser'/f'{platform}-login.log'
 with log.open('wb') as f:
  os.chmod(log,0o600)
  p=subprocess.Popen([sys.executable,'-m','leadworkbench.sources.social_browser','--login',str(directory),platform],stdout=f,stderr=f,start_new_session=True)
 SESSIONS[key]=p
 return {'status':'open','message':'已启动专用浏览器，请手动登录；完成后点击关闭登录窗口，再开始采集'}

def close_session(directory,platform):
 p=SESSIONS.pop((str(directory),platform),None)
 if p and p.poll() is None:
  p.terminate()
  try:p.wait(timeout=8)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
 return {'status':'closed','message':'登录窗口已关闭，专用会话保留在本机；未验证登录是否有效'}

def parse_browser_document(html,url,max_posts=3):
 max_posts=max(0,min(max_posts,5))
 soup=BeautifulSoup(html[:4_000_000],'html.parser')
 title=soup.title.get_text(' ',strip=True) if soup.title else ''
 blocked=page_gate(soup,url)
 if blocked:return {**blocked,'provider':'browser','source_url':url}
 result=parse_public_profile(html,url)
 posts=[];seen=set()
 for article in soup.select('article, [role="article"]')[:30] if max_posts else []:
  for a in article.select('a[href]'):
   target=classify_link(urljoin(url,a['href']))
   if target['kind']!='post' or target['platform']!=classify_link(url)['platform'] or target['url'] in seen:continue
   seen.add(target['url']);t=article.find('time');body=article.get_text(' ',strip=True)[:4000]
   posts.append({'url':target['url'],'text':body,'published_at':t.get('datetime','') if t else ''});break
  if len(posts)>=max_posts:break
 if posts and result['status']=='unreadable':result={'status':'preview_read','title':title[:300],'description':'','fields':[]}
 return {**result,'provider':'browser','source_url':url,'posts':posts,'message':result.get('message','')+'；仅保存实际加载的页面内容，帖子数量不代表全部历史'}

async def browser_context(p,settings,headless):
 from playwright.async_api import async_playwright
 driver=await async_playwright().start()
 args={'headless':headless,'chromium_sandbox':True,'accept_downloads':False,'viewport':{'width':1280,'height':900}}
 chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
 if chrome.exists():args['executable_path']=str(chrome)
 # X login uses ordinary Chrome and its OS credential store. Reads must use
 # that same store, rather than Playwright's default mock keychain.
 if Path(p).name=='x' and chrome.exists():
  args['ignore_default_args']=['--password-store=basic','--use-mock-keychain']
 if settings.get('proxy_url'):args['proxy']={'server':settings['proxy_url']}
 try:context=await driver.chromium.launch_persistent_context(str(p),**args)
 except BaseException:await driver.stop();raise
 return driver,context

async def read_browser(url,directory,settings):
 progress={}
 try:
  async with asyncio.timeout(90):
   return await _read_browser(url,directory,settings,_progress=progress)
 except TimeoutError:
  return _read_failure(progress.get('result'),'network_error','浏览器读取超过90秒预算，已关闭本次读取；请检查连接后重试')

def _read_failure(result,reason,message):
 if result and result.get('status')=='preview_read':
  if result.get('reason') in {'login_required','challenge','access_denied','rate_limited'}:
   return dict(result)
  return {**result,'partial':True,'reason':reason,'message':result.get('message','')+'；'+message+'；保留已读取资料'}
 return {'status':'failed','reason':reason,'provider':'browser','message':message}

def _merge_profile(previous,current):
 """Later sparse successful renders cannot erase fields collected this visit."""
 if previous.get('status')!='preview_read':return current
 keys={field['key'] for field in current.get('fields',[])}
 return {**current,
         'title':current.get('title') or previous.get('title',''),
         'description':current.get('description') or previous.get('description',''),
         'fields':current.get('fields',[])+[field for field in previous.get('fields',[]) if field['key'] not in keys]}

async def _read_browser(url,directory,settings,*,_progress=None):
 entry=classify_link(url);platform=entry['platform']
 if platform not in PLATFORMS or entry['kind'] not in {'account','post'}:return {'status':'blocked','reason':'unsupported','provider':'browser','message':'该平台未接入浏览器读取'}
 if not isinstance(settings,dict) or type(settings.get('max_posts',3)) is not int:
  return {'status':'blocked','reason':'invalid_settings','provider':'browser','message':'帖子数量配置无效'}
 settings={**settings,'max_posts':max(0,min(settings.get('max_posts',3),5))}
 if settings['max_posts']==0:settings['profile_only']=True
 progress={} if _progress is None else _progress
 def remember(result):
  progress['result']=result
  return result
 try:
  with profile_lock(directory,platform) as p:
   driver,context=await browser_context(p,settings,True)
   try:
    async def guard(route):
     from .website import normalize_url
     try:
      normalize_url(route.request.url)
      host=urlsplit(route.request.url).hostname or ''
      if route.request.is_navigation_request() and route.request.frame==page.main_frame and not(host==platform+'.com' or host.endswith('.'+platform+'.com')):return await route.abort()
     except Exception:return await route.abort()
     await route.continue_()
    page=await context.new_page();await page.route('**/*',guard)
    response=await page.goto(entry['url'],wait_until='domcontentloaded',timeout=35000)
    if response and response.status>=400:
     return {'status':'blocked' if response.status in {401,403,429} else 'failed',
             'reason':'rate_limited' if response.status==429 else 'access_denied' if response.status in {401,403} else 'network_error' if response.status>=500 else 'http_error',
             'provider':'browser','message':f'平台返回 HTTP {response.status}'}
    # Bounded waits permit client-rendered public profile content to arrive.
    await page.wait_for_timeout(1500)
    html=await page.content()
    if settings.get('profile_only'):
     from .social_profile import parse_profile
     result=remember(parse_profile(html,page.url))
     current=result
     for _ in range(4):
      if result['status'] in {'auth_required','challenge','blocked'}:break
      # Give profile regions time to render even when OG metadata arrives first.
      if len(result.get('fields',[]))>=2:break
      await page.wait_for_timeout(1000)
      current=parse_profile(await page.content(),page.url)
      if current['status'] in {'auth_required','challenge','blocked'}:
       if result['status']=='preview_read':
        result=remember(_read_failure(result,current.get('reason','profile_unrecognized'),current['message']))
       else:result=remember(current)
       break
      if current['status']=='preview_read':
       result=remember(_merge_profile(result,current))
      elif result['status']!='preview_read':
       result=remember(current)
     if current['status']=='unreadable' and result['status']=='preview_read':
      result=remember(_read_failure(result,'profile_unrecognized','后续资料结构未能识别，已停止本次读取'))
     return result
    result=remember(parse_browser_document(html,page.url,settings.get('max_posts',3)))
    if result['status'] not in {'auth_required','challenge','blocked'}:
     collected={p['url']:p for p in result.get('posts',[])}
     candidates={}
     stopped=False
     for _ in range(2):
      for a in BeautifulSoup(html,'html.parser').select('a[href]'):
       r=classify_link(urljoin(page.url,a['href']))
       if r['kind']=='post' and r['platform']==platform:candidates[r['url']]=True
      if len(collected)>=settings.get('max_posts',3):break
      try:
       await page.mouse.wheel(0,700);await page.wait_for_timeout(700)
       html=await page.content()
       current=parse_browser_document(html,page.url,settings.get('max_posts',3))
      except Exception:
       result.update(partial=True,reason='network_error')
       result['message']+='；继续读取未完成，已保留已读取内容'
       stopped=True
       break
      if current['status'] in {'auth_required','challenge','blocked'}:
       result.update(partial=True,reason=current.get('reason') or ('challenge' if current['status']=='challenge' else 'login_required'))
       result['message']+='；继续读取受到平台限制，已停止'
       stopped=True
       break
      collected.update({p['url']:p for p in current.get('posts',[])})
      result['posts']=list(collected.values())[:settings.get('max_posts',3)]
     # Instagram grids expose permalinks without article text; fetch only those
     # actually discovered on the profile, never invent post URLs.
     for target in list(candidates)[:settings.get('max_posts',3)]:
      if stopped:break
      if len(collected)>=settings.get('max_posts',3):break
      if target in collected:continue
      try:
       response=await page.goto(target,wait_until='domcontentloaded',timeout=15000)
       if response and response.status>=400:
        reason='rate_limited' if response.status==429 else 'login_required' if response.status in {401,403} else 'network_error' if response.status>=500 else 'http_error'
        result.update(partial=True,reason=reason)
        result['message']+=f'；帖子返回 HTTP {response.status}，已停止读取';break
       await page.wait_for_timeout(700)
       post=parse_browser_document(await page.content(),page.url,1)
       if post['status'] in {'auth_required','challenge','blocked'}:
        result.update(partial=True,reason=post.get('reason') or ('challenge' if post['status']=='challenge' else 'login_required'))
        result['message']+='；帖子读取受到平台限制，已停止';break
       if post.get('description'):
        collected[target]={'url':target,'text':post['description'],'published_at':''}
       elif post.get('posts'):
        collected.update({p['url']:p for p in post['posts']})
       result['posts']=list(collected.values())[:settings.get('max_posts',3)]
      except Exception:
       result.update(partial=True,reason='network_error')
       result['message']+='；帖子读取未完成';break
     result['posts']=list(collected.values())[:settings.get('max_posts',3)]
     if result['posts'] and result['status']=='unreadable':
      result.update(status='preview_read',title=result.get('title',''),description=result.get('description',''),fields=result.get('fields',[]))
     if result['status']=='preview_read' and not result['posts']:
      result['partial']=True
      result['message']+='；本次只读到简介，未读取到帖子，可登录后重试；不代表账号没有帖子'
    return result
   finally:
    try:await context.close()
    finally:await driver.stop()
 except ProfileBusy as e:return {'status':'blocked','reason':'session_busy','provider':'browser','message':str(e)}
 except ImportError:return {'status':'blocked','reason':'missing_dependency','provider':'browser','message':'浏览器依赖未安装，请运行 scripts/install_social.sh'}
 except Exception as e:return _read_failure(progress.get('result'),'network_error',f'浏览器读取失败：{type(e).__name__}；检查网络代理或重新登录')

def login_diagnostic(event,url='',status=None):
 """Allowlisted metadata only: never log request bodies, headers or full URLs."""
 from datetime import datetime,timezone
 try:u=urlsplit(url);host=u.hostname or ''
 except ValueError:host=''
 allowed={'x.com','twitter.com','twimg.com','instagram.com','facebook.com','fbcdn.net'}
 domain=next((d for d in allowed if host==d or host.endswith('.'+d)),'other')
 path=urlsplit(url).path if host else ''
 area='onboarding' if 'onboarding' in path else 'login' if 'login' in path else 'resource'
 out={'time':datetime.now(timezone.utc).isoformat(),'event':event,'host':domain,'area':area}
 if isinstance(status,int):out['status']=status
 elif status:
  error=re.search(r'ERR_[A-Z_]+',str(status))
  out['failure']=error[0] if error else 'unspecified'
 return out

def native_login_command(path,settings):
 chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
 args=[chrome,'--user-data-dir='+str(path),'--no-first-run','--no-default-browser-check']
 if settings.get('proxy_url'):args.append('--proxy-server='+settings['proxy_url'])
 return args+['https://x.com/']

async def native_x_login(directory):
 """Manual native login, isolated from the user's normal Chrome profile."""
 import signal
 from ..social_connections import load_connections
 with profile_lock(directory,'x') as path:
  child=await asyncio.create_subprocess_exec(*native_login_command(path,load_connections(directory)),
        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
  done=asyncio.Event();loop=asyncio.get_running_loop()
  for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,done.set)
  exited=asyncio.create_task(child.wait());requested=asyncio.create_task(done.wait())
  try:
   await asyncio.wait([exited,requested],return_when=asyncio.FIRST_COMPLETED)
  finally:
   if child.returncode is None:
    child.terminate()
    try:await asyncio.wait_for(child.wait(),5)
    except TimeoutError:child.kill();await child.wait()
   requested.cancel()
   await asyncio.gather(requested,exited,return_exceptions=True)

async def login(directory,platform):
 if platform=='x' and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').exists():
  return await native_x_login(directory)
 import signal
 from ..social_connections import load_connections
 with profile_lock(directory,platform) as p:
  driver,context=await browser_context(p,load_connections(directory),False)
  done=asyncio.Event();loop=asyncio.get_running_loop()
  for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,done.set)
  context.on('close',lambda:done.set())
  try:
   page=context.pages[0] if context.pages else await context.new_page()
   # Bounded diagnostics make stalled platform login actionable without storing
   # credentials, arbitrary script messages, or personal page content.
   import json
   count=0
   def report(event,url='',status=None):
    nonlocal count
    if count>=200:return
    count+=1
    print(json.dumps(login_diagnostic(event,url,status)),file=sys.stderr,flush=True)
   page.on('requestfailed',lambda request:report('request_failed',request.url,request.failure))
   page.on('response',lambda response:report('response',response.url,response.status)
           if response.status>=400 or 'onboarding' in urlsplit(response.url).path else None)
   page.on('pageerror',lambda error:report('javascript_error'))
   page.on('crash',lambda:report('page_crashed'))
   report('diagnostics_started')
   try:await page.goto(PLATFORMS[platform],timeout=35000,wait_until='domcontentloaded')
   except Exception as exc:report('navigation_failed',PLATFORMS[platform],type(exc).__name__)
   await done.wait()
  finally:await context.close();await driver.stop()

if __name__=='__main__':
 if len(sys.argv)==4 and sys.argv[1]=='--login':asyncio.run(login(sys.argv[2],sys.argv[3]))
