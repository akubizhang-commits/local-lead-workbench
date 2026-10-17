"""Route known social accounts to explicitly selected local providers."""
from ..social_projection import social_target as classify_link
from .social_discovery import read_public

async def read_combined(url,directory,settings,*,access_policy=None):
    entry=classify_link(url)
    if entry['kind'] not in {'account','post'}:
        return {'status':'blocked','reason':'unsupported_url','message':'地址不是可读取的公开账号或帖子'}
    platform=entry['platform']
    if platform=='youtube':
        from pathlib import Path
        import json
        path=Path(directory)/'social-secrets.json'
        if path.exists():
            try:
                with path.open('rb') as stream:
                    raw=stream.read(65537)
                if len(raw)>65536:raise ValueError
                config=json.loads(raw)
                if not isinstance(config,dict):raise ValueError
            except (OSError,ValueError,RecursionError):
                return {'status':'blocked','reason':'not_configured','provider':'youtube',
                        'message':'YouTube 配置无法读取，请检查本机配置后继续'}
            if config.get('youtube_api_key'):
                from .social_optional import read_youtube
                return await read_youtube(url,directory,settings)
    if platform=='instagram' and settings.get('instagram_reader')=='instaloader':
        from .social_optional import read_instaloader
        return await read_instaloader(url,directory,settings)
    if platform in {'instagram','facebook','x'} and settings.get('reader')=='browser':
        from .social_browser import read_browser
        return await read_browser(url,directory,settings)
    return {**await read_public(url,'read',access_policy=access_policy),'provider':'http'}
