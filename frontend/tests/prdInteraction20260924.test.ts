import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

const account = (id:string) => ({
 id,platform:'facebook',kind:'account',url:`https://example.invalid/${id}`,
 label:`Account ${id}`,status:'confirmed',scope:'brand',revision:1,
 linked_company_count:1,last_observed_at:null,evidence:[],
});
const socialData = (extra:Record<string,unknown>={}) => ({
 accounts:[account('a'),account('b')],posts:[],excluded:[],ignored_links:[],
 outreach:[],do_not_contact:false,search_queries:[],pages:[],...extra,
});
const flush = (h:Awaited<ReturnType<typeof setupDOM>>) =>
 h.act(async()=>{await new Promise(resolve=>setTimeout(resolve,20));});
async function setValue(h:Awaited<ReturnType<typeof setupDOM>>,element:HTMLInputElement|HTMLTextAreaElement,value:string) {
 await h.act(async()=>{
  const proto=element.tagName==='TEXTAREA'?h.dom.window.HTMLTextAreaElement.prototype:h.dom.window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto,'value')!.set!.call(element,value);
  element.dispatchEvent(new h.dom.window.Event('input',{bubbles:true}));
 });
}
function button(text:string) {
 const found=[...document.querySelectorAll('button')].find(b=>b.textContent===text);
 assert.ok(found,`Missing button: ${text}`);
 return found;
}
function mockSocial(data:ReturnType<typeof socialData>) {
 const calls:string[]=[];
 globalThis.fetch=async(input,init)=>{
  const path=String(input);calls.push(`${init?.method||'GET'} ${path}`);
  if(path==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
  assert.equal(init?.method||'GET','GET','Review must never send or write');
  assert.match(path,/^\/api\/companies\/fixture\/social$/);
  return new Response(JSON.stringify(data));
 };
 return calls;
}

test('PRD IT01: changing country must not retain the previous state and city',async()=>{
 const h=await setupDOM();
 globalThis.fetch=async()=>new Response(JSON.stringify([]));
 try {
  const {TaskForm}=await import('../src/forms.tsx');
  await h.act(async()=>h.root.render(createElement(TaskForm,{onDone:()=>{},onCancel:()=>{}})));
  await flush(h);
  await setValue(h,document.querySelector('input[name=state]')!,'California');
  await setValue(h,document.querySelector('input[name=city]')!,'Los Angeles');
  await setValue(h,document.querySelector('input[name=country]')!,'CA');
  assert.equal(document.querySelector<HTMLInputElement>('input[name=state]')!.value,'');
  assert.equal(document.querySelector<HTMLInputElement>('input[name=city]')!.value,'');
 } finally {await h.cleanup();}
});

test('PRD IT07: switching targets must not silently attach A draft to B',async()=>{
 const h=await setupDOM();
 mockSocial(socialData());
 h.dom.window.confirm=()=>false; // If a discard guard exists, cancel and retain A.
 try {
  const {SocialPanel}=await import('../src/SocialPanel.tsx');
  await h.act(async()=>h.root.render(createElement(SocialPanel,{companyId:'fixture',version:0,accountId:'a'})));
  await flush(h);
  await h.act(async()=>button('记录跟进 / 草稿').click());
  await setValue(h,document.querySelector('#social-followup textarea')!,'Only intended for account A');
  const select=document.querySelector<HTMLSelectElement>('#social-followup select')!;
  await h.act(async()=>{select.value='b';select.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});
  const target=document.querySelector<HTMLSelectElement>('#social-followup select')!.value;
  const note=document.querySelector<HTMLTextAreaElement>('#social-followup textarea')!.value;
  assert.ok(target!=='b'||note!=='Only intended for account A','B inherited A content without explicit copying');
 } finally {await h.cleanup();}
});

test('PRD-10: manually recorded replies must identify their manual provenance',async()=>{
 const h=await setupDOM();
 mockSocial(socialData({outreach:[{id:'o',company_name:'Fixture',platform:'facebook',
  label:'Account a',url:'https://example.invalid/a',state:'replied',note:'User entered this',
  created_at:'2026-09-24T00:00:00Z'}]}));
 try {
  const {SocialPanel}=await import('../src/SocialPanel.tsx');
  await h.act(async()=>h.root.render(createElement(SocialPanel,{companyId:'fixture',version:0,accountId:'a'})));
  await flush(h);
  assert.match(document.querySelector('.social-history article')!.textContent!,/人工/);
 } finally {await h.cleanup();}
});

test('PRD refusal: blocked company retains history without editable follow-up or sends',async()=>{
 const h=await setupDOM();
 const calls=mockSocial(socialData({do_not_contact:true}));
 try {
  const {SocialPanel}=await import('../src/SocialPanel.tsx');
  await h.act(async()=>h.root.render(createElement(SocialPanel,{companyId:'fixture',version:0,accountId:'a'})));
  await flush(h);
  assert.equal(document.querySelector('#social-followup form'),null);
  assert.ok(![...document.querySelectorAll('button')].some(b=>b.textContent==='记录跟进 / 草稿'));
  assert.ok(calls.every(call=>call.startsWith('GET ')));
 } finally {await h.cleanup();}
});

test('PRD IX-01: removed social account provides an explicit back action',async()=>{
 const h=await setupDOM();
 mockSocial(socialData());
 let back=false;
 try {
  const {SocialPanel}=await import('../src/SocialPanel.tsx');
  await h.act(async()=>h.root.render(createElement(SocialPanel,{companyId:'fixture',version:0,
   accountId:'missing',onBack:()=>{back=true;}})));
  await flush(h);
  assert.match(document.body.textContent!,/未关联当前企业或记录已移除/);
  await h.act(async()=>button('← 返回企业详情').click());
  assert.equal(back,true);
 } finally {await h.cleanup();}
});

test('PRD stale loading: a late response for A cannot replace the selected B detail',async()=>{
 const h=await setupDOM();
 let resolveA!:(value:Response)=>void;
 let requestedA=false;
 globalThis.fetch=async input=>{
  const path=String(input);
  if(path==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
  if(path==='/api/detail/a'){requestedA=true;return await new Promise<Response>(resolve=>{resolveA=resolve;});}
  assert.equal(path,'/api/detail/b');
  return new Response(JSON.stringify({name:'B'}));
 };
 try {
  const {useResource}=await import('../src/hooks.ts');
  function Detail({id}:{id:string}) {
   const resource=useResource<{name:string}>(`/detail/${id}`);
   return createElement('div',null,resource.data?.name||'loading');
  }
  await h.act(async()=>h.root.render(createElement(Detail,{id:'a'})));
  await flush(h);assert.equal(requestedA,true);
  await h.act(async()=>h.root.render(createElement(Detail,{id:'b'})));
  await flush(h);
  await h.act(async()=>resolveA(new Response(JSON.stringify({name:'A'}))));
  await flush(h);
  assert.equal(document.querySelector('#test-root')!.textContent,'B');
 } finally {await h.cleanup();}
});

test('PRD IT08: company/account panel remount retains unsaved target content', async()=>{
 const h=await setupDOM();
 mockSocial(socialData());
 try {
  const {SocialPanel}=await import('../src/SocialPanel.tsx');
  await h.act(async()=>h.root.render(createElement(SocialPanel,{key:'detail',companyId:'fixture',version:0,accountId:'a'})));
  await flush(h);
  await h.act(async()=>button('记录跟进 / 草稿').click());
  await setValue(h,document.querySelector('#social-followup textarea')!,'Retain across navigation');
  await h.act(async()=>h.root.render(createElement('div',null,'Company overview')));
  await h.act(async()=>h.root.render(createElement(SocialPanel,{key:'list',companyId:'fixture',version:0,accountId:'a'})));
  await flush(h);
  assert.equal(document.querySelector<HTMLTextAreaElement>('#social-followup textarea')!.value,'Retain across navigation');
  assert.equal(document.querySelector<HTMLSelectElement>('#social-followup select')!.value,'a');
  h.dom.window.confirm=()=>true;
  const select=document.querySelector<HTMLSelectElement>('#social-followup select')!;
  await h.act(async()=>{select.value='b';select.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});
  assert.equal(document.querySelector<HTMLTextAreaElement>('#social-followup textarea')!.value,'');
  assert.equal(select.value,'b');
 } finally {await h.cleanup();}
});
