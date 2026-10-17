import {useEffect,useState} from 'react';
import {api} from './api';
import {useResource} from './hooks';
import {Alert,Field} from './ui';
import {SocialDiscovery,socialStatus} from './SocialDiscovery';
import {SocialConnections} from './SocialConnections';

type Settings={searxng_url:string;configured:boolean;platforms:string[];message:string};
type Probe={status:string;message:string};

export function SocialSettings() {
  const [revision,setRevision]=useState(0);
  const settings=useResource<Settings>('/social/settings',revision);
  const [url,setUrl]=useState(''),[busy,setBusy]=useState(false),[failure,setFailure]=useState(''),[notice,setNotice]=useState('');
  const [probe,setProbe]=useState<Probe>();
  const [connectionsBlocked,setConnectionsBlocked]=useState(true);
  useEffect(()=>{if(settings.data)setUrl(settings.data.searxng_url);},[settings.data]);
  const dirty=url.trim()!==(settings.data?.searxng_url || '');
  async function save() {
    if(busy)return;
    setBusy(true);setFailure('');setNotice('');setProbe(undefined);
    try{await api('/social/settings','PUT',{searxng_url:url.trim()});setNotice('搜索服务配置已保存。');setRevision(v=>v+1);}
    catch(error){setFailure(error instanceof Error?error.message:'保存失败');}
    finally{setBusy(false);}
  }
  async function testConnection() {
    if(busy)return;
    setBusy(true);setFailure('');setNotice('');setProbe(undefined);
    try{setProbe(await api<Probe>('/social/probe','POST'));}
    catch(error){setFailure(error instanceof Error?error.message:'连接测试失败');}
    finally{setBusy(false);}
  }
  return <section className="panel social-settings">
    <div className="section-title"><h3>社媒自动发现</h3><span className="badge">{settings.loading?'读取配置中':settings.error?'配置状态未知':settings.data?.configured?'已配置搜索服务':'搜索服务未配置'}</span></div>
    <p className="muted">配置本机 SearxNG 搜索服务，需开启 JSON 搜索响应。搜索只提供候选；平台公开页面是否可读会单独记录。</p>
    {settings.error&&<Alert>{settings.error}<button onClick={()=>setRevision(v=>v+1)}>重读配置</button></Alert>}
    {failure&&<Alert>{failure}</Alert>}{notice&&<p role="status">{notice}</p>}
    <form onSubmit={e=>{e.preventDefault();void save();}}>
      <Field label="SearxNG 本机 URL"><input type="url" value={url} disabled={busy||settings.loading||!!settings.error} maxLength={4096} placeholder="http://127.0.0.1:8080" onChange={e=>{setUrl(e.target.value);setProbe(undefined);setNotice('');}}/></Field>
      <p className="muted">留空可停用搜索服务；官网发现和公开预览的实际结果以运行报告为准。</p>
      <div className="actions"><button className="primary" disabled={busy||settings.loading||!!settings.error}>{busy?'处理中…':'保存配置'}</button><button type="button" disabled={busy||settings.loading||!!settings.error||dirty||!settings.data?.configured} onClick={()=>void testConnection()}>测试连接</button></div>
      {dirty&&<p className="muted">地址已修改，请先保存，再测试已保存的服务。</p>}
    </form>
    {settings.data&&<><p className="muted">{settings.data.message}</p><p className="muted">发现平台：{settings.data.platforms.join('、') || '尚未提供'}。平台列表不代表访问能力已验证。</p></>}
    {probe&&<div className={`social-probe social-state-${probe.status}`} role="status"><strong>连接测试：{socialStatus(probe.status)}</strong><p>{probe.message || '服务未返回详细说明'}</p></div>}
    <SocialConnections onBlockedChange={setConnectionsBlocked}/>
    <SocialDiscovery disabled={busy||settings.loading||!!settings.error||dirty||connectionsBlocked}/>
  </section>;
}
