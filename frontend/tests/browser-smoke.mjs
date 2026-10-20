// Run after building to /tmp; all API responses and writes are synthetic.
// NODE_PATH=<bundled node_modules> node frontend/tests/browser-smoke.mjs
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {join,extname} from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const build=process.env.FRONTEND_TEST_DIST||'/tmp/lead-frontend-u1-u5-dist';
const binary=process.env.CHROMIUM_EXECUTABLE||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const requests=[];
let conflict=true;
const c={id:'fixture-51',name:'测试采购企业',address:'测试路 51 号',phone:'123',website:'https://fixture.example',city:'测试市',country:'US',business_types:['wholesaler'],status:'candidate',notes:'',revision:1,created_at:'2026-01-01T00:00:00Z',last_checked_at:'2026-09-01T00:00:00Z',manual_checked_at:null,contact_count:3,procurement_count:1,email_count:1};
const contact=(id,name,adoption_status,review_status)=>({id,name,kind:'procurement',role:'Buyer',role_status:'explicit',association_status:'same_block',email:`${id}@fixture.example`,url:'',source_url:'https://fixture.example/team',evidence:name,observed_at:'2026-09-01',adoption_status,review_status,fields:[{key:'email',label:'邮箱',value:`${id}@fixture.example`,source_url:'https://fixture.example/team',source_label:'官网',observed_at:'2026-09-01'}]});
const contacts=[contact('buyer','可用采购人员','automatic','valid'),contact('pending','待确认采购人员','accepted','pending'),contact('excluded','排除采购人员','excluded','pending'),contact('legacy','历史排除人员',undefined,'reject')];
const related=Array.from({length:54},(_,i)=>({...c,id:`shop-${i}`,name:`关联门店 ${i}`,address:`其他路 ${i} 号`}));
const overview={companies:55,contacts:3,tasks:1,pending_reviews:0,queued_runs:1,running_runs:0,worker:{alive:false,last_seen:null},source_status:{maps:{available:true,message:'Synthetic'},website:{available:true,message:'Synthetic'}}};
const market={id:'m',name:'测试市场',country:'US',state:'CA',city:'测试市',keywords:['clothing'],company_types:['wholesale'],auto_enrich:false,created_at:'2026-01-01',latest_run:null};
let exportJob=null;
const exportJobBase={id:'job-1',status:'succeeded',phase:'done',format:'csv',layout:'entries',filters:{},label:'联系入口 CSV',processed:1,total:1,rows:1,file_name:'fixture.csv',size_bytes:30,created_at:'2026-09-22T08:00:00Z',started_at:'2026-09-22T08:00:00Z',finished_at:'2026-09-22T08:00:01Z',error:null};
const server=createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://local');let body='';for await(const chunk of req)body+=chunk;
  const json=body?JSON.parse(body):null;requests.push({path:url.pathname,query:Object.fromEntries(url.searchParams),method:req.method,body:json});
  const reply=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(url.pathname==='/api/session')return reply({token:'synthetic'});
  if(url.pathname==='/api/overview')return reply(overview);
  if(url.pathname==='/api/tasks')return reply([market]);
  if(url.pathname==='/api/schedules')return reply([{id:'schedule',company_id:c.id,task_id:null,kind:'refresh',interval_months:3,timezone:'Asia/Shanghai',next_due_at:'2026-09-22',enabled:true,waiting_reason:'旧任务已暂停，本期计划等待处理'}]);
  if(url.pathname==='/api/runs')return reply([{id:'r',task_id:'m',kind:'discovery',status:'queued',progress:{done:0,total:1,discovered:0},message:'等待本地 worker',created_at:'2026-09-22',finished_at:null,error:null},{id:'exhausted',task_id:'m',kind:'discovery',status:'partial',progress:{done:1,total:1,discovered:120},message:'覆盖未完成',created_at:'2026-09-22',finished_at:'2026-09-22',error:null,coverage:{retryable:false,capability_exhausted:true,unresolved:['密集区域无法继续覆盖']}}]);
  if(url.pathname==='/api/company-page')return reply({items:[{...c}],total:55,page:Number(url.searchParams.get('page')||1),page_size:50});
  if(url.pathname==='/api/companies/batch-status'){
   if(conflict){conflict=false;return reply({detail:'测试修订冲突'},409);}
   c.status=json.status;c.revision++;c.manual_checked_at='2026-09-22T08:00:00Z';return reply({updated:json.companies.length});
  }
  if(url.pathname==='/api/companies/fixture-51'){
   if(req.method==='PATCH'){Object.assign(c,json.fields||{});if(json.status)c.status=json.status;if(json.business_types)c.business_types=json.business_types;c.revision++;c.manual_checked_at='2026-09-22T08:00:00Z';return reply(c);}
   return reply({company:c,contacts,related_locations:related,observations:[],changes:[],social_links:[]});
  }
  // Export jobs (the synchronous export endpoint was removed in P1 batch 2): create -> poll -> download file.
  if(url.pathname==='/api/export-jobs'&&req.method==='POST'){exportJob={...exportJobBase,filters:json,format:json.format,layout:json.layout};return reply(exportJob,202);}
  if(url.pathname==='/api/export-jobs')return reply(exportJob?[exportJob]:[]);
  if(url.pathname==='/api/export-jobs/job-1')return reply(exportJob);
  if(url.pathname==='/api/export-jobs/job-1/file'){res.writeHead(200,{'Content-Type':'text/csv','Content-Disposition':'attachment; filename="fixture.csv"'});return res.end('id,name\nfixture-51,Synthetic\n');}
  if(url.pathname.startsWith('/api/'))return reply([]);
  const file=url.pathname.startsWith('/assets/')?join(build,url.pathname):join(build,'index.html');
  res.writeHead(200,{'Content-Type':{'.js':'text/javascript','.css':'text/css','.html':'text/html'}[extname(file)]||'application/octet-stream'});res.end(await readFile(file));
 }catch(error){res.writeHead(500);res.end(String(error));}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;let testPage;
try{
 browser=await chromium.launch({executablePath:binary,headless:true});
 const page=testPage=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const base=`http://127.0.0.1:${server.address().port}`;
 await page.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 await page.goto(base+'/?view=companies&page=2&market_id=m&q=测试&contact=procurement&sort=email');
 await page.getByRole('button',{name:'查看 测试采购企业',exact:true}).click();
 assert.equal(new URL(page.url()).searchParams.get('company'),c.id);
 await page.reload();
 await page.getByRole('heading',{name:'测试采购企业',exact:true}).waitFor();
 assert.equal(await page.getByRole('heading',{name:'可用采购人员',exact:true}).isVisible(),true);
 assert.equal(await page.getByRole('heading',{name:'排除采购人员',exact:true}).isVisible(),false);
 assert.equal(await page.getByRole('heading',{name:'待确认采购人员',exact:true}).isVisible(),false);
 await page.getByText('等待原因：旧任务已暂停，本期计划等待处理',{exact:true}).waitFor();
 assert.equal(await page.locator('.related-locations').getAttribute('open'),null);
 assert.equal(await page.locator('.related-locations .location-card').first().isVisible(),false);
 const order=await page.locator('.other-contacts, .related-locations').evaluateAll(nodes=>nodes.map(n=>n.className));assert.ok(order.at(-1).includes('related-locations'));
 assert.match(await page.locator('.section-title').filter({hasText:'采购联系人'}).innerText(),/1/);
 await page.locator('.related-locations summary').click();await page.getByRole('textbox',{name:'搜索关联门店'}).fill('其他路 53');
 assert.equal(await page.locator('.related-locations .location-card').count(),1);
 await page.getByRole('button',{name:'编辑资料',exact:true}).click();
 await page.getByRole('checkbox',{name:'制造商',exact:true}).check();await page.getByRole('button',{name:'保存修正'}).click();
 await page.locator('.toast').filter({hasText:'企业资料已保存'}).waitFor({timeout:8000});
 assert.ok(requests.find(r=>r.method==='PATCH'&&r.body?.business_types?.includes('manufacturer')));
 await page.goBack();await page.getByRole('button',{name:'查看 测试采购企业',exact:true}).waitFor();
 assert.equal(new URL(page.url()).searchParams.get('page'),'2');assert.equal(await page.getByRole('textbox',{name:'搜索企业',exact:true}).inputValue(),'测试');
 await page.goForward();await page.getByRole('heading',{name:'测试采购企业',exact:true}).waitFor();
 await page.goBack();await page.getByRole('button',{name:'查看 测试采购企业',exact:true}).waitFor();
 assert.equal(new URL(page.url()).searchParams.get('page'),'2');
 await page.getByRole('combobox',{name:'联系入口筛选'}).selectOption('email');
 await page.getByRole('combobox',{name:'企业排序'}).selectOption('updated');
 assert.equal(new URL(page.url()).searchParams.get('page'),'1');
 await page.getByRole('checkbox',{name:'选择 测试采购企业',exact:true}).check();
 await page.getByRole('combobox',{name:'批量客户匹配状态',exact:true}).selectOption('verified');
 await page.getByRole('button',{name:'应用到所选企业'}).click();
 await page.getByText(/测试修订冲突/).waitFor();
 assert.equal(await page.getByRole('checkbox',{name:'选择 测试采购企业',exact:true}).isChecked(),false);
 assert.equal(requests.filter(r=>r.path==='/api/companies/batch-status').length,1);
 await page.getByRole('checkbox',{name:'选择 测试采购企业',exact:true}).check();await page.getByRole('button',{name:'应用到所选企业'}).click();
 await page.locator('.toast').filter({hasText:'所选企业的客户匹配状态已保存'}).waitFor();
 assert.equal(requests.filter(r=>r.path==='/api/companies/batch-status').length,2);
 await page.getByRole('combobox',{name:'测试采购企业 客户匹配状态',exact:true}).selectOption('not_fit');
 await page.locator('.toast').filter({hasText:'客户匹配状态已保存'}).waitFor();
 await page.getByRole('button',{name:'CSV',exact:true}).click();
 const exportRequest=requests.findLast(r=>r.path==='/api/export-jobs'&&r.method==='POST');assert.equal(exportRequest.body.contact,'email');assert.equal(exportRequest.body.sort,'updated');assert.equal(exportRequest.body.page,undefined);
 const dl=page.waitForEvent('download');await page.getByRole('button',{name:'下载文件',exact:true}).click();await dl;
 assert.ok(requests.some(r=>r.path==='/api/export-jobs/job-1/file'));assert.ok(requests.every(r=>!r.path.startsWith('/api/export')||r.path.startsWith('/api/export-jobs')));
 await page.getByRole('button',{name:'目标市场',exact:true}).click();
 await page.getByText('后台离线',{exact:true}).first().waitFor();
 assert.match(await page.locator('.worker-health').innerText(),/本地服务连接正常，不代表后台在运行/);
 assert.match(await page.locator('.run-card').filter({hasText:'运行编号：r'}).locator('.queue-reason').innerText(),/请启动本地后台/);
 const exhausted=page.locator('.run-card').filter({hasText:'运行编号：exhausted'});
 assert.equal(await exhausted.getByRole('button',{name:'继续本轮',exact:true}).count(),0);
 assert.match(await exhausted.innerText(),/当前策略已达到覆盖能力边界，重复继续无法补齐；可定期更新已知市场资料/);
 await exhausted.getByText('查看详细日志',{exact:true}).click();assert.match(await exhausted.innerText(),/密集区域/);
 const metrics=await page.locator('.metrics').innerText();assert.match(metrics,/排队中的轮次\s+1/);assert.match(metrics,/运行中的轮次\s+0/);
 await page.screenshot({path:'/tmp/lead-frontend-u1-u5-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/lead-frontend-u1-u5-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('PASS: URL reload/back + list filters/export + contact exclusion + shop search + business edit + batch conflict/success + quick status + worker heartbeat');
}catch(error){console.error('Recent requests',requests.slice(-8));if(testPage){await testPage.screenshot({path:'/tmp/lead-frontend-failure.png',fullPage:true});console.error((await testPage.locator('body').innerText()).slice(-3500));}throw error;}finally{await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
