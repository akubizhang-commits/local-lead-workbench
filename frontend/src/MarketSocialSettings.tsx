import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Alert} from './ui';
import type {Run,SocialPlatform,Task,TaskSocialSettings} from './types';

export const socialPlatforms:SocialPlatform[]=['facebook','instagram','x'];
export const platformTitle:Record<SocialPlatform,string>={facebook:'Facebook',instagram:'Instagram',x:'X'};
export const newMarketSocialSettings=():TaskSocialSettings=>({
 auto_social:true,social_platforms:[...socialPlatforms],social_read_posts:false,
});
function taskSettings(task:Task):TaskSocialSettings {
 return {auto_social:task.auto_social??false,social_platforms:task.social_platforms??[...socialPlatforms],
  social_read_posts:task.social_read_posts??false};
}

export function MarketSocialOptions({value,onChange,disabled=false}:{value:TaskSocialSettings;onChange:(value:TaskSocialSettings)=>void;disabled?:boolean}) {
 return <fieldset className="market-social-options" disabled={disabled}>
  <legend>社媒检索与补全</legend>
  <label className="check-line"><input type="checkbox" name="auto_social" checked={value.auto_social}
   onChange={e=>onChange({...value,auto_social:e.target.checked})}/>发现企业后自动检索社媒，与官网补全并行</label>
  <div className="checks" role="group" aria-label="社媒平台">
   {socialPlatforms.map(platform=><label key={platform}><input type="checkbox" name="social_platforms" value={platform}
    checked={value.social_platforms.includes(platform)}
    onChange={e=>onChange({...value,social_platforms:socialPlatforms.filter(item=>item===platform?e.target.checked:value.social_platforms.includes(item))})}/>{platformTitle[platform]}</label>)}
  </div>
  <label className="check-line"><input type="checkbox" name="social_read_posts" checked={value.social_read_posts}
   onChange={e=>onChange({...value,social_read_posts:e.target.checked})}/>同时读取近期帖子</label>
  <p className="hint">默认只检索账号和补充基础资料；候选账号仍需核对归属。平台需要登录或恢复时会保留进度，等待处理。</p>
 </fieldset>;
}

export function MarketSocialSettings({task,busy=false,onChange,onRun}:{task:Task;busy?:boolean;onChange:()=>void;onRun:(run:Run)=>void}) {
 const [value,setValue]=useState(()=>taskSettings(task));
 const [pending,setPending]=useState<'settings'|'social'|null>(null);
 const [error,setError]=useState(''),[notice,setNotice]=useState('');
 const inFlight=useRef(false);
 const savedSignature=JSON.stringify(taskSettings(task));
 useEffect(()=>{setValue(JSON.parse(savedSignature) as TaskSocialSettings);},[task.id,savedSignature]);
 async function submit(action:'settings'|'social') {
  if(busy||inFlight.current)return;
  setError('');setNotice('');
  if((action==='social'||value.auto_social)&&!value.social_platforms.length){
   setError('请至少选择一个社媒平台');return;
  }
  inFlight.current=true;setPending(action);
  try {
   const path=`/tasks/${encodeURIComponent(task.id)}`;
   if(action==='settings'){
    await api(`${path}/social-settings`,'PATCH',value);
    setNotice('已保存，仅影响后续自动检索设置；已有队列保持原设置。');
   } else {
    const run=await api<Run>(`${path}/social`,'POST',{
     social_platforms:value.social_platforms,social_read_posts:value.social_read_posts,
    });
    setNotice('社媒补充已加入队列，请查看该轮进度。');
    onRun(run);
   }
   onChange();
  } catch(error) {setError(error instanceof Error?error.message:'操作失败，请重试');}
  finally {inFlight.current=false;setPending(null);}
 }
 return <div className="market-social-settings" aria-label="市场社媒设置">
  <div className="row between"><p className="muted">后续自动检索社媒：{task.auto_social?'已开启':'已关闭'}</p>
   <button type="button" disabled={busy||!!pending} onClick={()=>void submit('social')}>
    {pending==='social'?'正在补充…':'补充社媒资料'}
   </button></div>
  <p className="hint">补充范围：{value.social_platforms.map(platform=>platformTitle[platform]).join(' / ')||'尚未选择平台'} · {value.social_read_posts?'基础资料与近期帖子':'仅基础资料'}。加入当前活动轮次；已结束则新建仅社媒轮次，不改写历史轮次。</p>
  <details><summary>社媒设置 · 选择平台与帖子</summary>
   <form onSubmit={event=>{event.preventDefault();void submit('settings');}}>
    <MarketSocialOptions value={value} onChange={next=>{setValue(next);setNotice('');}} disabled={busy||!!pending}/>
    <p className="hint">保存后仅对后续自动检索生效；“补充社媒资料”使用当前选择，需要单独点击执行。</p>
    <button type="submit" disabled={busy||!!pending}>{pending==='settings'?'正在保存…':'保存后续设置'}</button>
   </form>
  </details>
  {error&&<Alert>{error}</Alert>}{notice&&<p role="status">{notice}</p>}
 </div>;
}
