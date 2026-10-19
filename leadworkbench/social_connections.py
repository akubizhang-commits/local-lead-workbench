"""Private local connection configuration; never include credentials in job snapshots."""
import importlib.util
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

DEFAULTS={'reader':'http','instagram_reader':'browser','proxy_url':'','max_posts':3}

def validate_proxy(value):
 value=value.strip()
 if not value:return ''
 try:
  u=urlsplit(value)
  valid=u.scheme in {'http','socks5'} and u.hostname in {'localhost','127.0.0.1','::1'} and u.port and u.port>=1024 and not (u.username or u.password or u.query or u.fragment) and u.path in {'','/'}
 except ValueError:valid=False
 if not valid:raise ValueError('代理请填写本机 http 或 socks5 地址及端口，不含账号密码')
 return value.rstrip('/')

def private_write(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_suffix('.tmp')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 os.chmod(temp,0o600)
 with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False)
 os.replace(temp,path)

def load_connections(directory):
 p=Path(directory)/'social-connections.json'
 return {**DEFAULTS,**(json.loads(p.read_text()) if p.exists() else {})}

def public_connections(directory):
 from .sources.social_browser import session_status
 p=Path(directory)/'social-secrets.json'
 secret=json.loads(p.read_text()) if p.exists() else {}
 return {**load_connections(directory),'youtube_key_configured':bool(secret.get('youtube_api_key')),
 'dependencies':{k:importlib.util.find_spec(v) is not None for k,v in [('browser','playwright'),('instaloader','instaloader')]},
 'sessions':{p:session_status(directory,p) for p in ('instagram','facebook','x')}}

_SAVE_LOCK=threading.Lock()
def save_connections(directory,values):
 with _SAVE_LOCK:return _save_connections(directory,values)
def _save_connections(directory,values):
 data=load_connections(directory)
 data.update({k:v for k,v in values.items() if k in DEFAULTS})
 data['proxy_url']=validate_proxy(data['proxy_url'])
 if data['reader'] not in {'http','browser'} or data['instagram_reader'] not in {'browser','instaloader'} or not 1<=data['max_posts']<=5:raise ValueError('无效采集配置')
 if 'youtube_api_key' in values:
  p=Path(directory)/'social-secrets.json';secrets=json.loads(p.read_text()) if p.exists() else {}
  secrets['youtube_api_key']=values['youtube_api_key'].strip();private_write(p,secrets)
 private_write(Path(directory)/'social-connections.json',data)
 return public_connections(directory)
