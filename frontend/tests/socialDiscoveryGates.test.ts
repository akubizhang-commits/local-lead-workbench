import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import type {SocialRun} from '../src/SocialDiscovery.tsx';

const gate=(state='waiting_configuration',extra={})=>({
  state,reason:'请检查已保存的连接',manual_required:true,next_attempt_at:null,...extra,
});
const run=(extra={}):SocialRun=>({
  id:'manual-run',status:'queued',created_at:'2026-09-25T00:00:00Z',
  summary:{total:1,pending:1},items:[],
  platform_gates:{youtube:gate(),instagram:gate('waiting_login',{reason:'请先登录专用会话'})},
  ...extra,
});
const button=(text:string)=>{
  const found=[...document.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent===text);
  assert.ok(found,`Missing button: ${text}`);return found;
};
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});

test('manual reports expose platform waits above folded details and keep cooldown recovery disabled',async()=>{
  const h=await setupDOM();
  const {SocialRunReport}=await import('../src/SocialDiscovery.tsx');
  try{
    await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:run({platform_gates:{
      youtube:gate(),
      searxng:gate('waiting_configuration',{reason:'聚合搜索服务不可用'}),
      instagram:gate('waiting_login',{reason:'请先登录专用会话'}),
      x:gate('waiting_recovery',{reason:'请求频率受限',manual_required:false,next_attempt_at:'2099-01-01T00:00:00Z'}),
      facebook:gate('pending',{reason:'按平台访问间隔等待',manual_required:false,next_attempt_at:'2099-01-01T00:00:00Z'}),
    }})})));
    const gates=document.querySelector('[aria-label="当前平台等待状态"]');
    assert.ok(gates);assert.equal(gates.closest('details'),null);
    for(const text of ['YouTube','Instagram','搜索服务','聚合搜索服务不可用','等待登录','请先登录专用会话','请求频率受限','下次尝试','2099','冷却']){
      assert.ok(gates.textContent?.includes(text),text);
    }
    assert.equal(button('恢复 YouTube').disabled,false);
    assert.equal(button('恢复 搜索服务').disabled,false);
    assert.equal(button('恢复 X').disabled,true);
    assert.equal(button('恢复 Facebook').disabled,true);
    assert.ok(gates.querySelector('a[href*="view=settings"]'));
  }finally{await h.cleanup();}
});

test('manual recovery is bodyless, refreshes both reports and calls onChange even before polled data changes',async()=>{
  const h=await setupDOM();
  const {SocialDiscovery}=await import('../src/SocialDiscovery.tsx');
  const requests:{path:string;method:string;body:unknown;token:string|null}[]=[];
  let changed=0;
  globalThis.fetch=async(input,init)=>{
    const path=String(input),method=init?.method||'GET';
    requests.push({path,method,body:init?.body,token:new Headers(init?.headers).get('X-Local-Token')});
    if(path==='/api/session')return response({token:'fixture'});
    if(path==='/api/social/platforms/youtube/resume')return response({platform:'youtube',state:'pending'});
    if(path==='/api/social/runs?company_id=fixture')return response([run()]);
    if(path==='/api/social/runs/manual-run')return response(run());
    throw new Error(`Unexpected request ${method} ${path}`);
  };
  try{
    await h.act(async()=>h.root.render(createElement(SocialDiscovery,{companyId:'fixture',onChange:()=>changed++})));
    const before=changed,listReads=requests.filter(r=>r.path.includes('?company_id=')).length;
    const detailReads=requests.filter(r=>r.path==='/api/social/runs/manual-run').length;
    await h.act(async()=>button('恢复 YouTube').click());
    const mutations=requests.filter(r=>r.method!=='GET');
    assert.deepEqual(mutations,[{path:'/api/social/platforms/youtube/resume',method:'POST',body:undefined,token:'fixture'}]);
    assert.equal(changed,before+1,'successful resume must notify the parent even if a cached report is unchanged');
    assert.equal(requests.filter(r=>r.path.includes('?company_id=')).length,listReads+1);
    assert.equal(requests.filter(r=>r.path==='/api/social/runs/manual-run').length,detailReads+1);
  }finally{await h.cleanup();}
});

test('recovery deduplicates clicks and preserves wait reasons on server conflict without notifying success',async()=>{
  const h=await setupDOM();
  const {SocialRunReport}=await import('../src/SocialDiscovery.tsx');
  let finish!:(value:Response)=>void,mutations=0,changed=0;
  globalThis.fetch=async(input)=>{
    if(String(input)==='/api/session')return response({token:'fixture'});
    assert.equal(String(input),'/api/social/platforms/youtube/resume');
    mutations++;
    return new Promise<Response>(resolve=>{finish=resolve;});
  };
  try{
    await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:run(),onChange:()=>changed++})));
    const restore=button('恢复 YouTube');
    await h.act(async()=>{restore.click();restore.click();});
    assert.equal(mutations,1);
    assert.equal(button('恢复 Instagram').disabled,true);
    await h.act(async()=>finish(response({detail:'平台仍在冷却，请稍后继续'},409)));
    assert.match(document.querySelector('[role="alert"]')!.textContent!,/仍在冷却/);
    assert.match(document.body.textContent!,/请检查已保存的连接/);
    assert.equal(changed,0);
    assert.equal(button('恢复 YouTube').disabled,false);
  }finally{await h.cleanup();}
});

test('cooldown ends on the displayed deadline without remounting and never sends an automatic resume',async t=>{
  t.mock.timers.enable({apis:['Date','setInterval'],now:1000});
  // Flush Node's experimental timer warning before the DOM helper captures
  // console.error specifically to assert that React emits no render warnings.
  await new Promise<void>(resolve=>setImmediate(resolve));
  const h=await setupDOM();
  const {SocialRunReport}=await import('../src/SocialDiscovery.tsx');
  let requests=0;
  globalThis.fetch=async()=>{requests++;return response({token:'fixture'});};
  try{
    await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:run({platform_gates:{
      youtube:gate('waiting_recovery',{manual_required:false,next_attempt_at:new Date(3000).toISOString()}),
    }})})));
    assert.equal(button('恢复 YouTube').disabled,true);
    await h.act(async()=>t.mock.timers.tick(1000));
    assert.equal(button('恢复 YouTube').disabled,true);
    await h.act(async()=>t.mock.timers.tick(1000));
    assert.equal(button('恢复 YouTube').disabled,false);
    assert.equal(requests,0,'cooldown expiry changes controls without sending a request');
  }finally{
    await h.cleanup();
    t.mock.timers.reset();
  }
});

test('disabled manual operations cannot resume and market child reports do not duplicate manual controls',async()=>{
  const h=await setupDOM();
  const {SocialRunReport}=await import('../src/SocialDiscovery.tsx');
  let requests=0;
  globalThis.fetch=async()=>{requests++;return response({token:'fixture'});};
  try{
    await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:run(),disabled:true})));
    assert.equal(button('恢复 YouTube').disabled,true);
    await h.act(async()=>button('恢复 YouTube').click());
    assert.equal(requests,0);
    await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:run({parent_run_id:'market-run'})})));
    assert.equal(document.querySelector('[aria-label="当前平台等待状态"]'),null);
    await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:run({platform_gates:undefined})})));
    assert.equal(document.querySelector('[aria-label="当前平台等待状态"]'),null);
  }finally{await h.cleanup();}
});
