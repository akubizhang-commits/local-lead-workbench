import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

test('manual contact validation mirrors the API rules',async()=>{
 const {validateManualContact,emptyManualContact}=await import('../src/ManualContactForm.tsx');
 const base=emptyManualContact();
 assert.equal(validateManualContact(base).channel,'请至少填写邮箱、电话或联系页面中的一项');
 assert.equal(validateManualContact({...base,email:'nope'}).email,'邮箱格式无效');
 assert.equal(validateManualContact({...base,phone:'12345'}).phone,'电话格式无效');
 assert.equal(validateManualContact({...base,phone:'+1 408 555 0100 x9'}).phone,'电话格式无效');
 assert.equal(validateManualContact({...base,url:'manual.example/contact'}).url,'网址必须以 http:// 或 https:// 开头');
 assert.deepEqual(validateManualContact({...base,name:'Bob Buyer',email:'bob@manual.example',procurement:true}),{});
 assert.deepEqual(validateManualContact({...base,phone:'+1 (408) 555-0100'}),{});
});

test('form labels every field, links errors, submits and shows 409 with refresh',async()=>{
 const h=await setupDOM();
 const bodies:unknown[]=[];
 let status=409;
 globalThis.fetch=async(input,init)=>{const url=String(input);
  if(url==='/api/session')return new Response(JSON.stringify({token:'t'}),{headers:{'Content-Type':'application/json'}});
  bodies.push(JSON.parse(String(init?.body)));
  return status===409?new Response(JSON.stringify({detail:'资料已更新，请刷新后重试'}),{status:409,headers:{'Content-Type':'application/json'}})
   :new Response(JSON.stringify({company:{id:'c'},contacts:[]}),{headers:{'Content-Type':'application/json'}});};
 try {
  const {ManualContactForm}=await import('../src/ManualContactForm.tsx');
  let saved=0,refreshed=0;
  await h.act(async()=>h.root.render(createElement(ManualContactForm,{companyId:'c',revision:3,onSaved:()=>{saved++;},onCancel:()=>{},onRefresh:()=>{refreshed++;}})));
  const labels=[...document.querySelectorAll('label')].map(l=>l.textContent!.trim());
  for(const name of ['姓名','岗位','邮箱','电话','联系页面','此人负责采购','备注'])assert.ok(labels.some(l=>l.startsWith(name)),name);
  assert.match(document.body.textContent!,/人工录入的联系人来源标记为“人工”，之后的自动采集不会覆盖。/);
  const input=(label:string)=>[...document.querySelectorAll('label')].find(l=>l.textContent!.startsWith(label))!.querySelector('input')! as HTMLInputElement;
  const type=async(label:string,value:string)=>h.act(async()=>{const el=input(label);const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')!.set!;setter.call(el,value);el.dispatchEvent(new window.Event('input',{bubbles:true}));});
  await type('邮箱','bad');
  await h.act(async()=>{(document.querySelector('form') as HTMLFormElement).requestSubmit();});
  assert.equal(input('邮箱').getAttribute('aria-describedby'),'manual-email-error');
  assert.equal(document.getElementById('manual-email-error')!.textContent,'邮箱格式无效');
  assert.equal(bodies.length,0);
  await type('邮箱','bob@manual.example');
  await type('姓名','Bob Buyer');
  await h.act(async()=>{input('此人负责采购').click();});
  await h.act(async()=>{(document.querySelector('form') as HTMLFormElement).requestSubmit();await new Promise(r=>setTimeout(r,20));});
  assert.deepEqual(bodies[0],{revision:3,name:'Bob Buyer',role:'',email:'bob@manual.example',phone:'',url:'',procurement:true,note:''});
  assert.match(document.querySelector('[role=alert]')!.textContent!,/资料已更新，请刷新后重试/);
  const refresh=[...document.querySelectorAll('button')].find(b=>b.textContent==='刷新')!;
  await h.act(async()=>{refresh.click();});
  assert.equal(refreshed,1);
  status=200;
  await h.act(async()=>{(document.querySelector('form') as HTMLFormElement).requestSubmit();await new Promise(r=>setTimeout(r,20));});
  assert.equal(saved,1);
 } finally {await h.cleanup();}
});

test('entry summary shows best entry, gap and next step',async()=>{
 const h=await setupDOM();
 try {
  const {ContactEntrySummary}=await import('../src/ContactEntrySummary.tsx');
  const company={id:'c',name:'Co',phone:'',contact_count:2,best_tier:'general_email',best_tier_label:'通用邮箱',best_tier_rank:5,
   gap:{code:'no_named_people',reason:'官网已扫描 12 页，未公开具名人员',next_step:'人工新增联系人，或补充社媒'}};
  const contacts=[{id:'k',name:'',role:'',email:'info@co.example',url:'',tier:'general_email',review_status:'auto_recorded',adoption_status:'automatic',fields:[]}];
  await h.act(async()=>h.root.render(createElement(ContactEntrySummary,{company,contacts,onAdd:()=>{},adding:false} as never)));
  const text=document.body.textContent!;
  assert.match(text,/最佳入口：通用邮箱 · info@co.example/);
  assert.match(text,/联系入口 2 个（已去重）/);
  assert.match(text,/缺口：官网已扫描 12 页，未公开具名人员/);
  assert.match(text,/下一步：人工新增联系人，或补充社媒/);
  assert.ok([...document.querySelectorAll('button')].some(b=>b.textContent==='新增联系人'));
 } finally {await h.cleanup();}
});
test('company list entry cell shows tier, deduplicated count and gap, legacy counts in the tooltip',async()=>{
 const h=await setupDOM();
 try {
  const {EntryCell}=await import('../src/pages/CompaniesPage.tsx');
  const company={id:'c',contact_count:3,procurement_count:0,email_count:2,social_count:1,best_tier:'named_unknown',best_tier_label:'具名·岗位未明确',best_tier_rank:3,
   gap:{code:'no_procurement',reason:'已有具名人员 1 位，但未找到采购岗位',next_step:'x'}};
  await h.act(async()=>h.root.render(createElement(EntryCell,{company} as never)));
  assert.equal(document.querySelector('.tier')!.className,'tier tier-mid');
  assert.match(document.body.textContent!,/具名·岗位未明确联系入口 3 个（已去重）已有具名人员 1 位，但未找到采购岗位/);
  assert.match(document.querySelector('[title]')!.getAttribute('title')!,/采购 0.*其他邮箱 2 · 其他社媒 1/);
 } finally {await h.cleanup();}
});
