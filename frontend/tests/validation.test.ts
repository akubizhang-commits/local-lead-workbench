import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import type {ApiError} from '../src/api.ts';
import {parseErrorDetail} from '../src/validation.ts';

// Shape recorded in the 2026-10-15 acceptance run (area4 F1), before backend translation.
const rawBody={detail:[
 {type:'value_error',loc:['body','website'],msg:'Value error, Non-public IP address',input:'http://10.0.0.5',ctx:{error:{}}},
 {type:'value_error',loc:['body'],msg:'Value error, 请选择目标企业类型或填写关键词',input:{},ctx:{error:{}}},
]};

async function failure(body:unknown,status=422) {
 const {checked}=await import('../src/api.ts');
 try {await checked(new Response(JSON.stringify(body),{status}));}
 catch(error){return error as ApiError;}
 throw new Error('checked() should reject');
}

test('422 detail arrays become per-field text without JSON or English prefixes',async()=>{
 const error=await failure(rawBody);
 assert.equal(error.status,422);
 assert.doesNotMatch(error.message,/\[\{|"type"|Value error,/);
 assert.match(error.message,/「官方网站」：输入不符合要求（Non-public IP address）/);
 assert.match(error.message,/请选择目标企业类型或填写关键词/);
 assert.deepEqual(error.fields.map(f=>f.field),['website']);
 const translated=await failure({detail:[{type:'value_error',loc:['body','website'],msg:'官网不能是内网、本机或保留 IP 地址'}]});
 assert.equal(translated.message,'「官方网站」：官网不能是内网、本机或保留 IP 地址');
});

test('string detail is shown as is; unknown shapes fall back to the status',async()=>{
 assert.equal((await failure({detail:'请先取消活动任务再恢复'},409)).message,'请先取消活动任务再恢复');
 assert.equal(parseErrorDetail(500,{}).message,'请求失败（500）');
 assert.equal(parseErrorDetail(422,{detail:[{loc:['query','page'],msg:'Input should be valid'}]}).message,
  '「page」：输入不符合要求（Input should be valid）');
});

test('manual company form shows the Chinese website error and marks the input invalid',async()=>{
 const h=await setupDOM();
 const savedFormData=globalThis.FormData;
 globalThis.FormData=h.dom.window.FormData as typeof FormData;
 try {
  globalThis.fetch=async(input,init)=>{
   if(String(input)==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
   assert.equal(String(input),'/api/companies');assert.equal(init?.method,'POST');
   return new Response(JSON.stringify({detail:[{type:'value_error',loc:['body','website'],msg:'官网不能是内网、本机或保留 IP 地址'},
    {type:'string_too_long',loc:['body','name'],msg:'不能超过 200 个字符'}]}),{status:422});
  };
  const {CompanyForm}=await import('../src/forms.tsx');
  await h.act(async()=>h.root.render(createElement(CompanyForm,{onDone:()=>{},onCancel:()=>{}})));
  await h.act(async()=>{document.querySelector('form')!.dispatchEvent(new h.dom.window.Event('submit',{bubbles:true,cancelable:true}));
   await new Promise(resolve=>setTimeout(resolve,15));});
  const alert=document.querySelector('[role="alert"]')!;
  assert.deepEqual([...alert.querySelectorAll('li')].map(li=>li.textContent),
   ['「官方网站」：官网不能是内网、本机或保留 IP 地址','「企业名称」：不能超过 200 个字符']);
  const website=document.querySelector('input[name="website"]')!;
  assert.equal(website.getAttribute('aria-invalid'),'true');
  assert.equal(website.getAttribute('aria-describedby'),alert.parentElement!.id);
  assert.equal(document.querySelector('input[name="city"]')!.getAttribute('aria-invalid'),null);
 } finally {globalThis.FormData=savedFormData;await h.cleanup();}
});

test('market form without keywords or a preset asks for one of them in Chinese',async()=>{
 const h=await setupDOM();
 const savedFormData=globalThis.FormData;
 globalThis.FormData=h.dom.window.FormData as typeof FormData;
 try {
  globalThis.fetch=async(input)=>{
   const path=String(input);
   if(path==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
   assert.ok(path.startsWith('/api/region')||path==='/api/industry-presets',`Unexpected request ${path}`);
   return new Response('[]');
  };
  const {TaskForm}=await import('../src/forms.tsx');
  await h.act(async()=>h.root.render(createElement(TaskForm,{onDone:()=>{},onCancel:()=>{}})));
  await h.act(async()=>{await new Promise(resolve=>setTimeout(resolve,15));});
  assert.match(document.body.textContent!,/只选企业类型不会生成检索词；选择预设行业时，会按所选企业类型生成对应检索词/);
  assert.doesNotMatch(document.body.textContent!,/服装行业默认检索词/);
  await h.act(async()=>{document.querySelector('form')!.dispatchEvent(new h.dom.window.Event('submit',{bubbles:true,cancelable:true}));});
  assert.equal(document.querySelector('[role="alert"]')!.textContent,'请填写检索关键词，或从预设行业中选择一个');
  assert.equal(document.querySelector('textarea[name="keywords"]')!.getAttribute('aria-invalid'),'true');
 } finally {globalThis.FormData=savedFormData;await h.cleanup();}
});

test('unreachable local service gives a Chinese hint instead of a TypeError',async()=>{
 const h=await setupDOM();
 try {
  globalThis.fetch=async()=>{throw new TypeError('Failed to fetch');};
  const {api,ApiError}=await import('../src/api.ts');
  await assert.rejects(api('/overview'),(error:unknown)=>error instanceof ApiError&&error.status===0&&error.message==='无法连接本地服务，请确认已启动');
 } finally {await h.cleanup();}
});
