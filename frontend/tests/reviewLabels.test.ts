import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {fieldValue,label}=await import('../src/helpers.ts');
test('fieldValue shows review values in Chinese',()=>{
 assert.equal(fieldValue('business_types','["retailer","manufacturer"]'),'零售商、制造商');
 assert.equal(fieldValue('business_types',['importer']),'进口贸易商');
 assert.equal(fieldValue('business_types','[]'),'未记录');
 assert.equal(fieldValue('business_types','not json'),'not json');
 assert.equal(fieldValue('business_types_evidence','["distributor"]'),'分销商');
 assert.equal(fieldValue('status','do_not_contact'),'拒绝联系');
 assert.equal(fieldValue('contact','{"name":"Ann Lee","role":"Buyer","email":"ann@example.org","url":""}'),
  '姓名 Ann Lee · 岗位 Buyer · 邮箱 ann@example.org');
 assert.equal(fieldValue('contact','{broken'),'{broken');
 assert.equal(fieldValue('phone','(408) 435-5020'),'(408) 435-5020');
 assert.equal(fieldValue('name',null),'未记录');
 assert.equal(label('business_types_evidence'),'业务类型线索（未采用）');
 assert.equal(label('importer'),'进口贸易商');
});
test('ReviewList compares business types in Chinese, not JSON',async()=>{
 const {ReviewList}=await import('../src/ReviewList.tsx');
 const html=renderToStaticMarkup(createElement(ReviewList,{busy:false,onOpen:()=>{},onDecision:()=>{},reviews:[{
  id:'r1',company_id:'c1',company_name:'Superior Roof Metals',kind:'field_update',field:'business_types',
  old_value:'["retailer"]',new_value:'["manufacturer"]',source_url:'https://superiorroofmetals.com/',evidence:'x',
  status:'pending',created_at:'2026-10-17T08:00:00Z'}]}));
 assert.match(html,/零售商<\/p>.*制造商<\/p>/s);
 assert.doesNotMatch(html,/\[&quot;|\["/);
});
test('ChangeList marks auto-filled values',async()=>{
 const {ChangeList}=await import('../src/pages/ChangeList.tsx');
 const base={company_id:'c1',company_name:'Superior',created_at:'2026-10-17T08:00:00Z'};
 const html=renderToStaticMarkup(createElement(ChangeList,{onOpen:()=>{},items:[
  {...base,id:'a',field:'business_types',old_value:'[]',new_value:'["retailer"]',reason:'自动补全：原值为空',auto_fill:true},
  {...base,id:'b',field:'phone',old_value:'1',new_value:'2',reason:'人工修正',auto_fill:false}]}));
 assert.match(html,/badge-auto_fill">自动补全</);
 assert.match(html,/自动补全（原值为空，未进入审核）/);
 assert.doesNotMatch(html,/自动补全：原值为空/);
 assert.match(html,/人工修正/);
 assert.equal((html.match(/badge-auto_fill/g)||[]).length,1);
 assert.match(html,/零售商/);
});
const plan={plan_id:'a'.repeat(64),total:3,rules:[
 {rule:'phone_format',label:'电话仅格式不同',count:2,samples:[{review_id:'r1',company_name:'Omni',field:'phone',old_value:'+1 408-435-5020',new_value:'(408) 435-5020'}]},
 {rule:'business_type_enum',label:'业务类型不在批发商 / 零售商 / 进口贸易商范围内',count:1,samples:[{review_id:'r2',company_name:'Superior',field:'business_types',old_value:'["retailer"]',new_value:'["manufacturer"]'}]}]};
async function panel(plans:unknown[],post:()=>Response){
 const h=await setupDOM();
 const calls:{path:string;method:string;body:unknown}[]=[];
 let reads=0;
 globalThis.fetch=async(input,init)=>{
  const path=String(input),method=init?.method||'GET';
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  calls.push({path,method,body:init?.body?JSON.parse(String(init.body)):undefined});
  if(method==='POST')return post();
  return new Response(JSON.stringify(plans[Math.min(reads++,plans.length-1)]));
 };
 const prompts:string[]=[];
 h.dom.window.confirm=(text?:string)=>{prompts.push(String(text));return true;};
 globalThis.window.confirm=h.dom.window.confirm;
 let done=0;
 const {ReviewCleanup}=await import('../src/ReviewCleanup.tsx');
 await h.act(async()=>h.root.render(createElement(ReviewCleanup,{onDone:()=>{done++;}})));
 const wait=()=>h.act(async()=>{await new Promise(r=>setTimeout(r,20));});
 await wait();
 const click=async(text:RegExp)=>{await h.act(async()=>[...document.querySelectorAll('button')].find(b=>text.test(b.textContent||''))!.click());await wait();};
 return {...h,calls,prompts,click,done:()=>done};
}
test('cleanup panel: texts, confirmation and success',async()=>{
 const empty={plan_id:'b'.repeat(64),total:0,rules:[]};
 const h=await panel([plan,empty],()=>new Response(JSON.stringify({closed:3,rules:{phone_format:2,business_type_enum:1}})));
 try {
  const text=document.body.textContent!;
  assert.match(text,/清理明显重复的审核/);
  assert.match(text,/清理只会把它们标为“已自动关闭”，不会修改企业资料，也不会删除记录。/);
  assert.match(text,/电话仅格式不同 · 2 条/);
  assert.match(text,/Superior：零售商 → 制造商/);
  await h.click(/^确认清理 3 条$/);
  assert.equal(h.prompts[0],'将关闭 3 条待审核项，企业资料保持不变。继续吗？');
  assert.deepEqual(h.calls.find(c=>c.method==='POST')!.body,{plan_id:'a'.repeat(64)});
  assert.match(document.body.textContent!,/已关闭 3 条明显重复的审核，企业资料未修改/);
  assert.equal(h.done(),1);
  assert.equal(document.querySelector('.review-cleanup'),null);  // Re-read plan is empty: panel hidden.
 } finally {await h.cleanup();}
});
test('cleanup panel: a changed plan shows the server text and re-reads the preview',async()=>{
 const changed={...plan,plan_id:'c'.repeat(64),total:4};
 const h=await panel([plan,changed],()=>new Response(JSON.stringify({detail:'待审核内容已变化，请重新预览后再确认清理。',code:'plan_changed'}),{status:409}));
 try {
  await h.click(/^确认清理 3 条$/);
  assert.match(document.body.textContent!,/待审核内容已变化，请重新预览后再确认清理。/);
  assert.equal(h.calls.filter(c=>c.method==='GET'&&c.path==='/api/review-cleanup').length,2);
  assert.match(document.body.textContent!,/确认清理 4 条/);
  assert.equal(h.done(),0);
 } finally {await h.cleanup();}
});
test('cleanup panel is not rendered when there is nothing to close',async()=>{
 const h=await panel([{plan_id:'d'.repeat(64),total:0,rules:[]}],()=>new Response('{}'));
 try {
  assert.equal(document.querySelector('.review-cleanup'),null);
  assert.doesNotMatch(document.body.textContent||'',/清理明显重复的审核/);
 } finally {await h.cleanup();}
});
