import './support/registerTsx.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
const {errorKind,errorInfo,requestIdSuffix,actionMessage,isPlainClientError}=await import('../src/errors.ts');
const {ApiError,checked}=await import('../src/api.ts');
type ApiError=InstanceType<typeof ApiError>;

test('errorKind maps status and codes',()=>{
 assert.equal(errorKind(new ApiError('x',0,[],'offline')),'offline');
 assert.equal(errorKind({status:0}),'offline');
 assert.equal(errorKind(new ApiError('x',404,[],'not_found')),'not_found');
 for(const code of ['readonly_storage','disk_full','storage_unavailable','db_corrupt'])assert.equal(errorKind({status:503,code}),'storage');
 for(const code of ['db_busy','maintenance'])assert.equal(errorKind({status:503,code}),'busy');
 assert.equal(errorKind({status:500,code:'internal'}),'server');
 assert.equal(errorKind({status:502}),'server');
 assert.equal(errorKind({status:404,code:'unknown_endpoint'}),'client');
 assert.equal(errorKind({status:409,code:'conflict'}),'client');
 assert.equal(errorKind(new Error('plain')),'client');
});

test('ApiError takes request_id from the body, falling back to X-Request-ID',async()=>{
 const body=new Response(JSON.stringify({detail:'服务出错',code:'internal',request_id:'req_body'}),{status:500,headers:{'X-Request-ID':'req_head'}});
 await assert.rejects(checked(body),(e:ApiError)=>e.requestId==='req_body'&&e.code==='internal'&&e.status===500);
 const header=new Response(JSON.stringify({detail:'服务出错'}),{status:502,headers:{'X-Request-ID':'req_head'}});
 await assert.rejects(checked(header),(e:ApiError)=>e.requestId==='req_head'&&e.code===undefined);
 const notJson=new Response('oops',{status:500,headers:{'X-Request-ID':'req_text'}});
 await assert.rejects(checked(notJson),(e:ApiError)=>e.requestId==='req_text');
});

test('action errors get the request id suffix only for 5xx',()=>{
 assert.equal(requestIdSuffix(new ApiError('x',500,[],'internal','req_1')),'（错误编号 req_1）');
 assert.equal(requestIdSuffix(new ApiError('x',409,[],'conflict','req_2')),'');
});

async function render(props:Record<string,unknown>){
 const {createElement}=await import('react');
 const {renderToStaticMarkup}=await import('react-dom/server');
 const {Resource}=await import('../src/Resource.tsx');
 return renderToStaticMarkup(createElement(Resource,{loading:false,retry:()=>{},children:'ok',...props} as never));
}

test('Resource: not_found has no reconnect and offers the detail-page action',async()=>{
 const html=await render({error:'企业不存在或已合并',errorInfo:errorInfo(new ApiError('企业不存在或已合并',404,[],'not_found','req_x')),notFoundAction:{label:'返回企业库',onClick:()=>{}}});
 assert.match(html,/记录已更新或移除/);
 assert.match(html,/返回企业库/);
 assert.doesNotMatch(html,/重新连接/);
 assert.doesNotMatch(html,/请确认本地服务已启动/);
 const plain=await render({error:'记录不存在',errorInfo:errorInfo(new ApiError('记录不存在',404,[],'not_found'))});
 assert.match(plain,/>刷新</);
});

test('Resource: server errors show detail and a copyable request id',async()=>{
 const html=await render({error:'服务出错，请稍后重试。',errorInfo:errorInfo(new ApiError('服务出错，请稍后重试。',500,[],'internal','req_3f9a1c2b7d4e'))});
 assert.match(html,/服务出错/);
 assert.match(html,/错误编号：<code>req_3f9a1c2b7d4e<\/code>/);
 assert.match(html,/重试/);
 const storage=await render({error:'磁盘空间不足，本次修改未保存。',errorInfo:errorInfo(new ApiError('磁盘空间不足',507,[],'disk_full','req_d'))});
 assert.match(storage,/<h3>磁盘空间不足<\/h3>/);
 const busy=await render({error:'x',errorInfo:errorInfo(new ApiError('x',503,[],'maintenance','req_m'))});
 assert.match(busy,/正在恢复备份/);
 assert.match(busy,/请稍后重试。/);
});

test('Resource: offline keeps the original reconnect text',async()=>{
 const html=await render({error:'无法连接本地服务，请确认已启动',errorInfo:errorInfo(new ApiError('无法连接本地服务，请确认已启动',0,[],'offline'))});
 assert.match(html,/暂时无法读取数据/);
 assert.match(html,/请确认本地服务已启动，然后重新连接/);
 assert.match(html,/重新连接/);
});

test('actionMessage: other 4xx get the "请求未完成：" prefix',()=>{
 assert.equal(actionMessage(new ApiError('企业没有可核验官网',400,[],'invalid')),'请求未完成：企业没有可核验官网');
 assert.equal(actionMessage(new ApiError('接口不存在',404,[],'unknown_endpoint')),'请求未完成：接口不存在');
 assert.equal(actionMessage(new ApiError('令牌无效',401)),'请求未完成：令牌无效');
 assert.equal(actionMessage(new ApiError('请求体过大',413)),'请求未完成：请求体过大');
 assert.equal(actionMessage(new ApiError('计划不存在',404,[],'not_found')),'请求未完成：计划不存在');
 assert.ok(isPlainClientError({status:404,code:'not_found'}));
 assert.ok(!isPlainClientError({status:409}));
 assert.ok(!isPlainClientError({status:422}));
});

test('actionMessage: 409, 422, offline, 5xx and plain errors',()=>{
 assert.equal(actionMessage(new Error('x')),'x');
 assert.equal(actionMessage(new Error('')),'操作未完成');
 assert.equal(actionMessage(new ApiError('资料已变化',409,[],'conflict')),'资料已变化。资料已变化，请刷新后重新选择或编辑；本次操作未重试。');
 const exists=actionMessage(new ApiError('该市场已有启用中的定时计划',409,[],'schedule_exists'));
 assert.equal(exists,'该市场已有启用中的定时计划');
 assert.equal(actionMessage(new ApiError('关键词不能为空',422,[],'validation')),'关键词不能为空');
 assert.equal(actionMessage(new ApiError('无法连接本地服务，请确认已启动',0,[],'offline')),'无法连接本地服务，请确认已启动');
 assert.equal(actionMessage(new ApiError('服务出错',500,[],'internal','req_9')),'服务出错（错误编号 req_9）');
 for(const status of [400,401,404,409,413,422,500])assert.doesNotMatch(actionMessage(new ApiError('m',status)),/重新连接|请确认本地服务已启动/);
});

test('Resource: plain client errors say "请求未完成" without reconnect text',async()=>{
 const html=await render({error:'企业没有可核验官网',errorInfo:errorInfo(new ApiError('企业没有可核验官网',400,[],'invalid'))});
 assert.match(html,/<h3>请求未完成<\/h3>/);
 assert.match(html,/请求未完成：企业没有可核验官网/);
 assert.match(html,/>重试</);
 assert.doesNotMatch(html,/重新连接/);
 assert.doesNotMatch(html,/请确认本地服务已启动/);
 const conflict=await render({error:'冲突',errorInfo:errorInfo(new ApiError('冲突',409,[],'conflict'))});
 assert.match(conflict,/<h3>暂时无法读取数据<\/h3>/);
 assert.doesNotMatch(conflict,/重新连接|请求未完成/);
});

test('Resource: not_found body starts with "请求未完成："',async()=>{
 const html=await render({error:'计划不存在',errorInfo:errorInfo(new ApiError('计划不存在',404,[],'not_found'))});
 assert.match(html,/<h3>记录已更新或移除<\/h3><p>请求未完成：计划不存在<\/p>/);
 assert.doesNotMatch(html,/重新连接/);
});
