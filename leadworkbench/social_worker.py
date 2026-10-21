"""One durable work item per worker turn, with source-specific outcomes."""
import asyncio
from .sources.social_discovery import read_public,search_searx

SERVER_SKIP={'status':'skipped','reason':'server_mode','message':'服务器部署版不支持站内搜索（需要登录社媒账号），已跳过'}
async def process_social_item(store,*,search=search_searx,read=read_public,platform_search=None,server=False):
    item=store.claim_social_item()
    if not item:return False
    async def execute():
        if item['stage']=='platform_search' and server:
            return dict(SERVER_SKIP)  # needs a logged-in session; never opens a browser on a server
        if item['stage']=='platform_search':
            reader=platform_search
            if reader is None:
                from .sources.social_platform_search import search_platform
                reader=search_platform
            with store.connect() as con:
                company=con.execute('SELECT name FROM companies WHERE id=?',(item['company_id'],)).fetchone()
            result=await reader(item['target'],company['name'],store.directory,item['settings'].get('connections',{}))
        elif item['stage']=='search':
            result=await search(item['settings'].get('searxng_url',''),item['target'])
        elif not item['target']:
            result={'status':'skipped','reason':'missing_website','message':'企业尚无官网，未发起网站请求'}
        else:
            # Shared brand accounts only fetched once in this run; every company still gets a coverage record.
            previous=item.get('cached_result')
            if previous:result=previous
            if previous is None:
                if read is read_public and item['stage']=='profile':
                    from .sources.social_browser import read_browser
                    result=await read_browser(item['target'],store.directory,{**item['settings'].get('connections',{}),'profile_only':True})
                elif read is read_public and item['stage']=='read':
                    from .sources.social_combination import read_combined
                    from .website_policy import WebsiteAccessPolicy
                    result=await read_combined(item['target'],store.directory,item['settings'].get('connections',{}),
                                              access_policy=WebsiteAccessPolicy(store))
                elif read is read_public:
                    from .website_policy import WebsiteAccessPolicy
                    result=await read(item['target'],item['stage'],access_policy=WebsiteAccessPolicy(store))
                else:
                    result=await read(item['target'],item['stage'])
        return result

    job=asyncio.create_task(execute())
    try:
        while not job.done():
            done,_=await asyncio.wait({job},timeout=0.5)
            if not store.social_item_active(item):
                job.cancel()
                await asyncio.gather(job,return_exceptions=True)
                # Completion rechecks exclusion; paused/cancelled parents reject
                # this too, so an old owner never resets a newly claimed item.
                store.finish_social_item(item['id'],{'status':'skipped','message':'读取已停止'},claim_token=item['claim_token'])
                return True
            if done:break
        result=await job
        store.finish_social_item(item['id'],result,claim_token=item['claim_token'])
    except asyncio.CancelledError:
        job.cancel()
        await asyncio.gather(job,return_exceptions=True)
        store.release_social_item(item)
        raise
    except Exception as exc:
        store.finish_social_item(item['id'],{'status':'failed','reason':'worker_error','message':f'{type(exc).__name__}: {str(exc)[:300]}'},claim_token=item['claim_token'])
    return True
