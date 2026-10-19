import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

const review=(id:string)=>({id,company_id:"c1",company_name:"Fixture Co",kind:"field_update",field:"notes",old_value:"",
 new_value:id,source_url:"https://fixture.example",evidence:"e",status:"pending",created_at:"2026-10-18T00:00:00Z",evidence_count:1,evidence_items:[]});
const json=(body:unknown)=>new Response(JSON.stringify(body),{headers:{"Content-Type":"application/json"}});

test("review pages: totals, paging and stepping back after the last item of a page",async()=>{
 const h=await setupDOM();
 let total=21;
 const requests:string[]=[];
 globalThis.fetch=async(input)=>{const url=String(input);
  if(url==="/api/session")return json({token:"t"});
  if(url.startsWith("/api/reviews?")){requests.push(url);
   const params=new URL(url,"http://x").searchParams;const page=Number(params.get("page"));const size=Number(params.get("page_size"));
   const ids=Array.from({length:total},(_,i)=>`r${i}`).slice((page-1)*size,page*size);
   return json({items:ids.map(review),total,page,page_size:size});}
  throw new Error("unexpected "+url);};
 try{
  const {ReviewsPage}=await import("../src/pages/ReviewsPage.tsx");
  const props={busy:false,act:async()=>{},refresh:()=>{},openCompany:()=>{}};
  const render=async(version:number)=>h.act(async()=>{h.root.render(createElement(ReviewsPage,{...props,version} as never));await new Promise(r=>setTimeout(r,40));});
  await render(0);
  assert.match(requests[0],/page=1&page_size=20/);
  assert.ok((document.body.textContent||"").includes("共 21 条待审核 · 第 1 / 2 页"),document.body.textContent||"");
  const button=(label:string)=>[...document.querySelectorAll(".review-pagination button")].find(b=>b.textContent===label) as HTMLButtonElement;
  assert.ok(button("上一页").disabled);
  await h.act(async()=>{button("下一页").click();await new Promise(r=>setTimeout(r,40));});
  assert.match(requests.at(-1)!,/page=2&page_size=20/);
  assert.ok((document.body.textContent||"").includes("第 2 / 2 页"),document.body.textContent||"");
  assert.ok(button("下一页").disabled);
  total=20; // the only review on page 2 was decided
  await render(1);
  await h.act(async()=>{await new Promise(r=>setTimeout(r,60));});
  assert.match(requests.at(-1)!,/page=1&page_size=20/);
  assert.ok((document.body.textContent||"").includes("共 20 条待审核 · 第 1 / 1 页"),document.body.textContent||"");
 }finally{await h.cleanup();}
});
