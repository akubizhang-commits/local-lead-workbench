import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

const task={id:'m1',name:'San Jose · 批发商 · metal roofing supply',country:'US',state:'California',city:'San Jose',
 keywords:['metal roofing supply'],company_types:['wholesale'],auto_enrich:false,created_at:'2026-10-15T00:00:00Z',
 latest_run:null,archived_at:null};
const estimate={terms:['metal roofing supply'],query_terms:['metal roofing supply in San Jose, California, US'],
 geometry:'catalogue',initial_min:17,initial_max:17,max:421};

async function dialog(respond:(path:string,method:string,body:Record<string,unknown>)=>Response|undefined=()=>undefined,props={}){
 const h=await setupDOM();
 const calls:{path:string;method:string;body:Record<string,unknown>}[]=[];
 globalThis.fetch=async(input,init)=>{
  const path=String(input),method=init?.method||'GET',body=init?.body?JSON.parse(String(init.body)):undefined;
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  calls.push({path,method,body});
  const custom=respond(path,method,body);
  if(custom)return custom;
  if(path==='/api/industry-presets')return new Response('[]');
  if(path==='/api/tasks/preview')return new Response(JSON.stringify({...estimate,keywords:body.keywords,existing_market:null,existing_market_scope:'edit'}));
  if(path.startsWith('/api/exclusion-rules')||path.startsWith('/api/company-categories'))return new Response('[]');
  return new Response(JSON.stringify(task));
 };
 const {MarketEditDialog}=await import('../src/MarketEditDialog.tsx');
 const opened:string[]=[];const saved:unknown[]=[];
 await h.act(async()=>h.root.render(createElement(MarketEditDialog,{task,version:0,busy:false,act:()=>{},
  onSaved:(t:unknown)=>saved.push(t),onCancel:()=>{},onOpenMarket:(id:string)=>opened.push(id),...props} as never)));
 const wait=(ms=20)=>h.act(async()=>{await new Promise(r=>setTimeout(r,ms));});
 await wait();
 const setKeywords=(value:string)=>h.act(async()=>{
  const node=document.querySelector<HTMLTextAreaElement>('textarea[name="keywords"]')!;
  Object.getOwnPropertyDescriptor(h.dom.window.HTMLTextAreaElement.prototype,'value')!.set!.call(node,value);
  node.dispatchEvent(new h.dom.window.Event('input',{bubbles:true}));
 });
 const submit=()=>h.act(async()=>document.querySelector('form[id]')!.dispatchEvent(new h.dom.window.Event('submit',{bubbles:true,cancelable:true})));
 return {...h,calls,opened,saved,wait,setKeywords,submit};
}

test('edit dialog: read-only region, hint, prefilled keywords and a preview that excludes this market',async()=>{
 const h=await dialog();
 try {
  const text=document.body.textContent!;
  assert.match(text,/地区：US \/ California \/ San Jose/);
  assert.match(text,/国家、州 \/ 省和城市创建后不能修改。要换地区，请新建市场。/);
  assert.equal(document.querySelector('input[name="city"]'),null);
  assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[name="keywords"]')!.value,'metal roofing supply');
  await h.wait(450);
  const preview=h.calls.find(c=>c.path==='/api/tasks/preview')!;
  assert.deepEqual(preview.body,{keywords:['metal roofing supply'],market_id:'m1'});
  assert.doesNotMatch(document.body.textContent!,/已有相同/);
  assert.match(document.body.textContent!,/首轮 17 次/);
 } finally {await h.cleanup();}
});

test('edit dialog: keywords are required',async()=>{
 const h=await dialog();
 try {
  await h.setKeywords('');
  await h.submit();
  assert.match(document.body.textContent!,/请填写检索关键词，或从预设行业中选择一个/);
  assert.equal(h.calls.some(c=>c.method==='PATCH'),false);
 } finally {await h.cleanup();}
});

test('edit dialog: another market with these keywords offers to open it, in the preview and on 409',async()=>{
 const h=await dialog((path,method,body)=>{
  if(path==='/api/tasks/preview'&&(body.keywords as string[])[0]==='sheet metal supplier')
   return new Response(JSON.stringify({...estimate,keywords:body.keywords,existing_market:{id:'m2',name:'Other',archived:false},existing_market_scope:'edit'}));
  if(method==='PATCH')return new Response(JSON.stringify({detail:'已有相同地区和关键词的目标市场「Other」，请直接使用该市场。',code:'market_exists',existing_id:'m2',archived:false}),{status:409});
  return undefined;
 });
 try {
  await h.setKeywords('sheet metal supplier');
  await h.wait(450);
  assert.match(document.body.textContent!,/已有相同地区和关键词的目标市场「Other」，保存时会被拒绝。请修改关键词，或直接打开该市场。/);
  await h.submit();
  await h.wait();
  assert.match(document.body.textContent!,/已有相同地区和关键词的目标市场「Other」，请直接使用该市场。/);
  const buttons=[...document.querySelectorAll('button')].filter(b=>b.textContent==='打开该市场');
  assert.equal(buttons.length,2);
  await h.act(async()=>buttons[0].click());
  assert.deepEqual(h.opened,['m2']);
 } finally {await h.cleanup();}
});

test('edit dialog: saving sends the keywords; an unfinished round is mentioned',async()=>{
 const h=await dialog(undefined,{task:{...task,latest_run:{id:'r',status:'paused'}}});
 try {
  assert.match(document.body.textContent!,/当前未结束的一轮仍按原关键词执行。/);
  await h.setKeywords('metal roofing supply, sheet metal supplier');
  await h.submit();
  await h.wait();
  assert.deepEqual(h.calls.find(c=>c.method==='PATCH')!.body,{keywords:['metal roofing supply','sheet metal supplier']});
  assert.equal(h.saved.length,1);
 } finally {await h.cleanup();}
});

async function marketsPage(tasks:unknown[]){
 const h=await setupDOM();
 const {MarketsPage}=await import('../src/pages/MarketsPage.tsx');
 const actions:string[]=[];
 await h.act(async()=>h.root.render(createElement(MarketsPage,{overview:{loading:false,error:''},tasks:{data:tasks,loading:false,error:''},
  busy:false,act:(_w:unknown,message:unknown)=>{actions.push(String(message));},refresh:()=>{},setTaskDetail:()=>{},
  openMarket:()=>{},openStandalone:()=>{},setModal:()=>{}} as never)));
 return {...h,actions};
}

test('archived markets move into a collapsible section with a restore button',async()=>{
 const h=await marketsPage([task,{...task,id:'m2',name:'Archived market',archived_at:'2026-10-18T00:00:00+00:00'}]);
 try {
  assert.match(document.querySelector('.count')!.textContent!,/^1$/);
  assert.match(document.querySelector('.archived-markets summary')!.textContent!,/已归档市场（1）/);
  const rows=[...document.querySelectorAll('tbody tr')].map(r=>r.textContent);
  assert.ok(rows[0]!.includes('San Jose · 批发商'));
  assert.ok(rows.some(r=>r!.includes('Archived market')&&r!.includes('恢复')));
  const restore=[...document.querySelectorAll('button')].find(b=>b.textContent==='恢复')!;
  await h.act(async()=>restore.click());
  assert.deepEqual(h.actions,['市场已恢复。定时计划保持原设置，下次到期后才会运行。']);
 } finally {await h.cleanup();}
});

async function activity(t:Record<string,unknown>,confirmAnswer=true){
 const h=await setupDOM('http://localhost/?view=tasks&task=m1');
 globalThis.fetch=async(input)=>{
  const path=String(input);
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  if(path.startsWith('/api/tasks/m1/activity'))return new Response(JSON.stringify({items:[],total:0,page:1,page_size:50,rounds:[],workflow:null}));
  if(path==='/api/tasks/m1/website-recheck')return new Response('null');
  return new Response('[]');
 };
 const prompts:string[]=[];
 h.dom.window.confirm=(text?:string)=>{prompts.push(String(text));return confirmAnswer;};
 globalThis.window.confirm=h.dom.window.confirm;
 const {TaskActivityPage}=await import('../src/pages/TaskActivityPage.tsx');
 const actions:string[]=[];
 await h.act(async()=>h.root.render(createElement(TaskActivityPage,{route:{view:'tasks',selected:null,filters:{q:'',status:'',type:''},page:1,task:'m1'},
  navigate:()=>{},tasks:[t],version:0,worker:undefined,busy:false,act:(_w:unknown,m:unknown)=>{actions.push(String(m));},
  refresh:()=>{},openCompany:()=>{},openMarket:()=>{}} as never)));
 await h.act(async()=>{await new Promise(r=>setTimeout(r,20));});
 return {...h,prompts,actions};
}

test('archive asks first; an archived market shows the banner, restore and a disabled schedule switch',async()=>{
 const h=await activity(task);
 try {
  const archive=[...document.querySelectorAll('button')].find(b=>b.textContent==='归档市场')!;
  await h.act(async()=>archive.click());
  assert.deepEqual(h.prompts,['归档后，这个市场不再出现在市场列表中，也不会按计划自动更新。企业、联系人和历史记录都会保留，可随时在“已归档市场”中恢复。继续吗？']);
  assert.deepEqual(h.actions,['市场已归档，可在“已归档市场”中恢复']);
 } finally {await h.cleanup();}
 const archived=await activity({...task,archived_at:'2026-10-18T00:00:00+00:00'});
 try {
  const text=document.body.textContent!;
  assert.match(text,/此市场已归档：不会开始新一轮，也不会按计划自动更新。企业和历史记录都已保留。/);
  assert.ok([...document.querySelectorAll('button')].some(b=>b.textContent==='恢复市场'));
  assert.equal([...document.querySelectorAll('button')].some(b=>b.textContent==='编辑市场'),false);
  assert.equal(document.querySelector<HTMLInputElement>('input[role="switch"]')!.disabled,true);
  assert.match(text,/已归档，恢复后可开启/);
 } finally {await archived.cleanup();}
});

test('legacy markets without keywords are asked to add some',async()=>{
 const h=await activity({...task,keywords:[],needs_keywords:true,legacy_terms:['apparel wholesaler','clothing wholesaler']});
 try {
  assert.match(document.body.textContent!,/这个市场创建时没有填写关键词，目前按旧规则使用服装行业默认检索词（apparel wholesaler、clothing wholesaler）。/);
 } finally {await h.cleanup();}
});

test('the market list asks for archived markets too',async()=>{
 const h=await setupDOM('http://localhost/?view=tasks');
 const paths:string[]=[];
 globalThis.fetch=async(input)=>{
  const path=String(input);paths.push(path);
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  if(path==='/api/overview')return new Response(JSON.stringify({companies:0,contacts:0,tasks:0,pending_reviews:0,running_runs:0,
   source_status:{maps:{available:false,message:''},website:{available:true,message:''}}}));
  if(path==='/api/tasks?archived=include')return new Response(JSON.stringify([task,{...task,id:'m2',name:'Old',archived_at:'2026-10-18T00:00:00+00:00'}]));
  return new Response('[]');
 };
 try {
  const {default:App}=await import('../src/App.tsx');
  await h.act(async()=>h.root.render(createElement(App)));
  await h.act(async()=>{await new Promise(r=>setTimeout(r,30));});
  assert.ok(paths.includes('/api/tasks?archived=include'));
  assert.equal(paths.includes('/api/tasks'),false);
  assert.match(document.body.textContent!,/已归档市场（1）/);
 } finally {await h.cleanup();}
});
