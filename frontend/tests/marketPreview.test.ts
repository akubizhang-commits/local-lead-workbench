import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
const {estimateLines,existingText,startConfirmText,presetTerms}=await import('../src/QueryPreview.tsx');

const presets=[
 {id:'building',label:'建材与屋面',terms:{wholesale:'roofing supply',retail:'building materials store',import:'building materials importer'}},
 {id:'metals',label:'金属材料',terms:{wholesale:'metal supplier',retail:'metal supply store',import:'steel importer'}},
];
const catalogue={terms:['metal roofing supply'],query_terms:['metal roofing supply in San Jose, California, US'],
 geometry:'catalogue' as const,initial_min:17,initial_max:17,max:421};

test('preview text for the three geometries',()=>{
 assert.equal(estimateLines(catalogue),'预计 Maps 查询：首轮 17 次（每个检索词 1 次城市检索 + 16 个分区）。结果密集的分区会自动细分，最多约 421 次；按低速模式逐个执行，可随时暂停。');
 assert.equal(estimateLines({...catalogue,geometry:'geocoder',initial_min:1,initial_max:17,max:null}),
  '预计 Maps 查询：首轮 1–17 次（城市范围定位成功时 17 次，失败时只做 1 次文字检索）。');
 assert.equal(estimateLines({...catalogue,geometry:'text_only',initial_min:1,initial_max:1,max:1}),'预计 Maps 查询：1 次文字检索。');
});

test('existing market hints for new and archived markets, and for the edit dialog',()=>{
 const base={...catalogue,keywords:['metal roofing supply']};
 assert.equal(existingText({...base,existing_market:null},'create'),'');
 assert.equal(existingText({...base,existing_market:{id:'a',name:'San Jose · 批发商',archived:false}},'create'),
  '已有相同的目标市场「San Jose · 批发商」，提交后会直接打开该市场，不会重复创建。');
 assert.equal(existingText({...base,existing_market:{id:'a',name:'San Jose · 批发商',archived:true}},'create'),
  '已有相同的目标市场「San Jose · 批发商」，目前已归档。可在“已归档市场”中恢复后继续使用。');
 assert.equal(existingText({...base,existing_market:{id:'b',name:'B',archived:false}},'edit'),
  '已有相同地区和关键词的目标市场「B」，保存时会被拒绝。请修改关键词，或直接打开该市场。');
});

test('start confirmation: with a maximum, without one, and a geocoder range',()=>{
 assert.equal(startConfirmText(catalogue),'本轮预计执行 17 次 Maps 查询（结果密集时最多约 421 次），按低速模式逐个执行，可随时暂停。开始吗？');
 assert.equal(startConfirmText({...catalogue,geometry:'text_only',initial_min:2,initial_max:2,max:2}),
  '本轮预计执行 2 次 Maps 查询，按低速模式逐个执行，可随时暂停。开始吗？');
 assert.equal(startConfirmText({...catalogue,geometry:'geocoder',initial_min:1,initial_max:17,max:null}),
  '本轮预计执行 1–17 次 Maps 查询，按低速模式逐个执行，可随时暂停。开始吗？');
});

test('preset terms follow the selected company types, wholesale when none',()=>{
 assert.deepEqual(presetTerms(presets[1],['retail']),['metal supply store']);
 assert.deepEqual(presetTerms(presets[1],[]),['metal supplier']);
 assert.deepEqual(presetTerms(presets[1],['import','wholesale']),['metal supplier','steel importer']);
});

async function form(){
 const h=await setupDOM();
 const savedFormData=globalThis.FormData;
 globalThis.FormData=h.dom.window.FormData as typeof FormData;
 const previews:Record<string,unknown>[]=[],created:Record<string,unknown>[]=[];
 globalThis.fetch=async(input,init)=>{
  const path=String(input);
  if(path==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
  if(path==='/api/industry-presets')return new Response(JSON.stringify(presets));
  if(path==='/api/tasks/preview'){
   const body=JSON.parse(String(init!.body));previews.push(body);
   return new Response(JSON.stringify({...catalogue,keywords:body.keywords.length?body.keywords:['metal supply store'],
    query_terms:(body.keywords.length?body.keywords:['metal supply store']).map((k:string)=>`${k} in ${body.city}, ${body.state}, US`),
    existing_market:null}));
  }
  if(path==='/api/tasks'){created.push(JSON.parse(String(init!.body)));return new Response(JSON.stringify({id:'t'}));}
  return new Response('[]');
 };
 const {TaskForm}=await import('../src/forms.tsx');
 await h.act(async()=>h.root.render(createElement(TaskForm,{onDone:()=>{},onCancel:()=>{}})));
 const wait=(ms:number)=>h.act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
 await wait(20);
 const setValue=async(selector:string,value:string)=>h.act(async()=>{
  const node=document.querySelector<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>(selector)!;
  const proto=node.tagName==='TEXTAREA'?h.dom.window.HTMLTextAreaElement.prototype:node.tagName==='SELECT'?h.dom.window.HTMLSelectElement.prototype:h.dom.window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto,'value')!.set!.call(node,value);
  node.dispatchEvent(new h.dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));
 });
 await setValue('input[name="state"]','California');
 await setValue('input[name="city"]','San Jose');
 await wait(20);
 const button=()=>document.querySelector<HTMLButtonElement>('button.primary')!;
 return {...h,previews,created,wait,setValue,button,
  async cleanup(){try{await h.cleanup();}finally{globalThis.FormData=savedFormData;}}};
}

test('presets come from the API; choosing one shows its expansion and previews it',async()=>{
 const h=await form();
 try {
  const options=[...document.querySelectorAll<HTMLOptionElement>('select[name="industry"] option')].map(o=>o.textContent);
  assert.deepEqual(options,['不使用预设','建材与屋面','金属材料']);
  await h.act(async()=>document.querySelector<HTMLInputElement>('input[name="company_types"][value="wholesale"]')!.click());
  await h.act(async()=>document.querySelector<HTMLInputElement>('input[name="company_types"][value="retail"]')!.click());
  await h.setValue('select[name="industry"]','metals');
  assert.match(document.body.textContent!,/将使用检索词：metal supply store/);
  await h.wait(450);
  assert.equal(h.previews.at(-1)!.industry,'metals');
  assert.match(document.body.textContent!,/metal supply store in San Jose, California, US/);
 } finally {await h.cleanup();}
});

test('the create button waits for a preview of the current input',async()=>{
 const h=await form();
 try {
  assert.match(document.body.textContent!,/填写关键词或选择预设行业后显示检索预览/);
  await h.setValue('textarea[name="keywords"]','metal roofing supply');
  assert.equal(h.button().disabled,true);
  assert.match(document.body.textContent!,/请先等待检索预览/);
  await h.wait(450);
  assert.equal(h.button().disabled,false);
  assert.match(document.body.textContent!,/首轮 17 次/);
  assert.match(document.body.textContent!,/最多约 421 次/);
  await h.setValue('textarea[name="keywords"]','metal roofing supply, sheet metal');
  assert.equal(h.button().disabled,true);
  await h.wait(450);
  await h.act(async()=>document.querySelector('form')!.dispatchEvent(new h.dom.window.Event('submit',{bubbles:true,cancelable:true})));
  await h.wait(20);
  assert.deepEqual(h.created[0].keywords,['metal roofing supply','sheet metal']);
 } finally {await h.cleanup();}
});

test('preview failures use the action message',async()=>{
 const h=await setupDOM();
 try {
  globalThis.fetch=async(input)=>String(input)==='/api/session'?new Response(JSON.stringify({token:'t'}))
   :new Response(JSON.stringify({detail:'城市与所选州不匹配',code:'invalid'}),{status:400});
  const {QueryPreview}=await import('../src/QueryPreview.tsx');
  await h.act(async()=>h.root.render(createElement(QueryPreview,{body:{keywords:['x'],state:'A',city:'B'}})));
  await h.act(async()=>{await new Promise(resolve=>setTimeout(resolve,450));});
  assert.match(document.body.textContent!,/暂时无法生成预览：请求未完成：城市与所选州不匹配/);
 } finally {await h.cleanup();}
});

test('starting a round asks first; cancelling sends nothing',async()=>{
 const h=await setupDOM();
 try {
  const {MarketsPage}=await import('../src/pages/MarketsPage.tsx');
  const prompts:string[]=[];let answer=false;
  h.dom.window.confirm=(text?:string)=>{prompts.push(String(text));return answer;};
  globalThis.window.confirm=h.dom.window.confirm;
  const actions:string[]=[];
  const task={id:'m',name:'Market',country:'US',state:'California',city:'San Jose',keywords:['metal roofing supply'],
   company_types:['wholesale'],auto_enrich:true,created_at:'2026-10-15T00:00:00Z',latest_run:null,query_estimate:catalogue};
  await h.act(async()=>h.root.render(createElement(MarketsPage,{overview:{loading:false,error:''},tasks:{data:[task],loading:false,error:''},
   busy:false,act:(_work:unknown,message:unknown)=>{actions.push(String(message));},refresh:()=>{},setTaskDetail:()=>{},
   openMarket:()=>{},openStandalone:()=>{},setModal:()=>{}} as never)));
  const start=[...document.querySelectorAll('button')].find(b=>b.textContent==='开始检索')!;
  await h.act(async()=>start.click());
  assert.deepEqual(prompts,['本轮预计执行 17 次 Maps 查询（结果密集时最多约 421 次），按低速模式逐个执行，可随时暂停。开始吗？']);
  assert.deepEqual(actions,[]);
  answer=true;
  await h.act(async()=>start.click());
  assert.deepEqual(actions,['市场更新已加入运行队列']);
 } finally {await h.cleanup();}
});
