import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

const base = {id:"a".repeat(32),status:"running",phase:"building",format:"xlsx",layout:"entries",filters:{format:"xlsx",layout:"entries",q:""},
 label:"联系入口 XLSX",processed:1234,total:20000,rows:0,file_name:"联系入口-2026-10-18.xlsx",size_bytes:null,
 created_at:"2026-10-18T00:00:00Z",started_at:"2026-10-18T00:00:00Z",finished_at:null,error:null};
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}});

async function mount(h:Awaited<ReturnType<typeof setupDOM>>){
 const {useExportJobs,ExportJobsPanel}=await import("../src/ExportJobs.tsx");
 let state:any;
 function Host(){state=useExportJobs(40);return createElement(ExportJobsPanel,{state});}
 await h.act(async()=>{h.root.render(createElement(Host));await new Promise(r=>setTimeout(r,30));});
 return ()=>state;
}

test("progress text, aria-valuetext and polling that stops when the job ends",async()=>{
 const h=await setupDOM();
 let job:any={...base};
 const polls:string[]=[];
 globalThis.fetch=async(input)=>{const url=String(input);
  if(url==="/api/session")return json({token:"t"});
  if(url.startsWith("/api/export-jobs?"))return json([job]);
  if(url===`/api/export-jobs/${base.id}`){polls.push(url);return json(job);}
  throw new Error("unexpected "+url);};
 try{
  await mount(h);
  const progress=document.querySelector("progress")!;
  assert.equal(progress.getAttribute("aria-valuetext"),"已处理 1,234 / 20,000 家企业");
  assert.equal(progress.getAttribute("max"),"20000");
  assert.equal(document.querySelector('[aria-live="polite"]')!.textContent,"正在处理企业…");
  assert.ok([...document.querySelectorAll("button")].some(b=>b.textContent==="取消"));
  job={...job,phase:"writing",rows:48210};
  await h.act(async()=>{await new Promise(r=>setTimeout(r,120));});
  assert.match(document.body.textContent||"",/正在写入文件（48,210 行）…/);
  job={...job,status:"succeeded",phase:"done",size_bytes:3*1024*1024,finished_at:"2026-10-18T00:00:21Z"};
  await h.act(async()=>{await new Promise(r=>setTimeout(r,120));});
  assert.match(document.body.textContent||"",/导出完成：联系入口 XLSX · 3.0 MB · 用时 21 秒/);
  assert.ok([...document.querySelectorAll("button")].some(b=>b.textContent==="下载文件"));
  assert.equal(document.querySelector("progress"),null);
  const count=polls.length;
  await h.act(async()=>{await new Promise(r=>setTimeout(r,200));});
  assert.equal(polls.length,count);
 }finally{await h.cleanup();}
});

test("busy start shows the running job and the waiting notice",async()=>{
 const h=await setupDOM();
 globalThis.fetch=async(input,init)=>{const url=String(input);
  if(url==="/api/session")return json({token:"t"});
  if(url==="/api/export-jobs"&&init?.method==="POST")return json({detail:"已有导出正在进行，请等待完成",code:"export_busy",job_id:base.id,request_id:"req_x"},409);
  if(url.startsWith("/api/export-jobs?"))return json([]);
  if(url===`/api/export-jobs/${base.id}`)return json(base);
  throw new Error("unexpected "+url);};
 try{
  const state=await mount(h);
  let started:unknown;
  await h.act(async()=>{started=await state().start({format:"csv",layout:"entries"});await new Promise(r=>setTimeout(r,20));});
  assert.equal(started,false);
  assert.match(document.body.textContent||"",/已有导出正在进行，请等待完成/);
  assert.match(document.body.textContent||"",/联系入口 XLSX/);
  assert.ok(state().active);
 }finally{await h.cleanup();}
});

test("failed and interrupted jobs offer re-export with the same parameters",async()=>{
 const h=await setupDOM();
 const failed={...base,status:"failed",phase:"done",error:{code:"disk_full",message:"磁盘空间不足，导出未完成。请清理磁盘后重新导出。"}};
 const interrupted={...base,id:"b".repeat(32),created_at:"2026-10-17T00:00:00Z",status:"failed",phase:"done",error:{code:"interrupted",message:"导出中断（本地服务已重启），请重新导出"}};
 const posts:any[]=[];
 globalThis.fetch=async(input,init)=>{const url=String(input);
  if(url==="/api/session")return json({token:"t"});
  if(url==="/api/export-jobs"&&init?.method==="POST"){posts.push(JSON.parse(String(init.body)));return json({...base,id:"c".repeat(32),created_at:"2026-10-19T00:00:00Z",status:"queued",phase:"queued"},202);}
  if(url.startsWith("/api/export-jobs?"))return json([failed,interrupted]);
  if(url.startsWith("/api/export-jobs/"))return json({...base,id:"c".repeat(32),created_at:"2026-10-19T00:00:00Z",status:"queued",phase:"queued"});
  throw new Error("unexpected "+url);};
 try{
  await mount(h);
  const text=document.body.textContent||"";
  assert.match(text,/导出失败：磁盘空间不足/);
  assert.match(text,/导出中断（本地服务已重启），请重新导出/);
  const retry=[...document.querySelectorAll("button")].find(b=>b.textContent==="重新导出") as HTMLButtonElement;
  await h.act(async()=>{retry.click();await new Promise(r=>setTimeout(r,30));});
  assert.equal(posts.length,1);
  assert.equal(posts[0].format,"xlsx");assert.equal(posts[0].layout,"entries");
  assert.match(document.body.textContent||"",/正在准备导出…/);
  assert.ok([...document.querySelectorAll("button")].filter(b=>b.textContent==="重新导出").every(b=>(b as HTMLButtonElement).disabled));
 }finally{await h.cleanup();}
});

test("exportBody keeps only non-empty filters",async()=>{
 const {exportBody}=await import("../src/ExportJobs.tsx");
 assert.deepEqual(exportBody({q:"",status:"",type:"",category:["Metal supplier",""],excluded:"",best_tier:"named_role"},"csv","entries"),
  {format:"csv",layout:"entries",category:["Metal supplier"],best_tier:"named_role"});
});
