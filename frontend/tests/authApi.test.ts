import './support/registerTsx.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
const api=await import('../src/api.ts');
const {actionMessage}=await import('../src/errors.ts');

type Call={url:string;method:string;token:string|null};
function stub(handler:(url:string,init:RequestInit)=>Response|Promise<Response>) {
 const calls:Call[]=[];
 globalThis.fetch=(async (input:RequestInfo|URL,init:RequestInit={})=>{
  const headers=new Headers(init.headers);
  calls.push({url:String(input),method:init.method||'GET',token:headers.get('X-Local-Token')});
  return handler(String(input),init);
 }) as typeof fetch;
 return calls;
}
const json=(body:unknown,status=200,headers:Record<string,string>={})=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json',...headers}});

test.beforeEach(()=>{api.resetSession();api.setOnUnauthorized(undefined);api.setServerMode(false);});

test('login() posts once without asking /api/session first, then reuses its token',async()=>{
 const calls=stub(url=>url==='/api/auth/login'?json({user:{username:'alice'},token:'csrf-1'}):json({ok:true}));
 const result=await api.login('alice','pw');
 assert.equal(result.user.username,'alice');
 assert.deepEqual(calls.map(c=>[c.method,c.url]),[['POST','/api/auth/login']]);
 await api.api('/overview');
 assert.deepEqual(calls.map(c=>[c.url,c.token]),[['/api/auth/login',null],['/api/overview','csrf-1']]);
});

test('login errors carry Retry-After for 429',async()=>{
 stub(()=>json({detail:'登录尝试过多，请 15 分钟后再试',code:'login_rate_limited'},429,{'Retry-After':'899'}));
 await assert.rejects(api.login('alice','pw'),(e:InstanceType<typeof api.ApiError>)=>e.status===429&&e.retryAfter===899);
});

test('an unauthenticated session raises 401 and calls the handler',async()=>{
 let fired=0;
 api.setOnUnauthorized(()=>{fired+=1;});
 stub(()=>json({authenticated:false,mode:'server',auth:true}));
 await assert.rejects(api.api('/overview'),(e:InstanceType<typeof api.ApiError>)=>e.status===401&&e.code==='unauthorized');
 assert.equal(fired,1);
});

test('a 401 response resets the session and calls the handler',async()=>{
 let fired=0;
 api.setOnUnauthorized(()=>{fired+=1;});
 let sessions=0;
 stub(url=>{if(url==='/api/session'){sessions+=1;return json({authenticated:true,mode:'server',token:'t'});}
  return json({detail:'请先登录',code:'unauthorized'},401);});
 await assert.rejects(api.api('/overview'));
 assert.equal(fired,1);
 await assert.rejects(api.api('/overview'));
 assert.equal(sessions,2);
 assert.equal(actionMessage(new api.ApiError('请先登录',401,[],'unauthorized')),'');
 api.setOnUnauthorized(undefined);
 assert.equal(actionMessage(new api.ApiError('请先登录',401,[],'unauthorized')),'请求未完成：请先登录');
});

test('csrf_failed refreshes the token and retries exactly once',async()=>{
 let tokens=0;
 const calls=stub(url=>{if(url==='/api/session'){tokens+=1;return json({authenticated:true,mode:'server',token:`t${tokens}`});}
  return json({detail:'页面已过期，请刷新后重试',code:'csrf_failed'},403);});
 await assert.rejects(api.api('/backups','POST'),(e:InstanceType<typeof api.ApiError>)=>e.code==='csrf_failed');
 assert.deepEqual(calls.filter(c=>c.url==='/api/backups').map(c=>c.token),['t1','t2']);
});

test('other 403 responses are not retried',async()=>{
 const calls=stub(url=>url==='/api/session'?json({token:'local'}):json({detail:'拒绝跨来源请求',code:'forbidden'},403));
 await assert.rejects(api.api('/backups','POST'));
 assert.equal(calls.filter(c=>c.url==='/api/backups').length,1);
});

test('logout() with an expired session posts without X-Local-Token',async()=>{
 const calls=stub(url=>url==='/api/session'?json({authenticated:false,mode:'server'}):new Response(null,{status:204}));
 await api.logout();
 const post=calls.find(c=>c.url==='/api/auth/logout');
 assert.ok(post);
 assert.equal(post.token,null);
});

test('logout() with a session sends its token',async()=>{
 const calls=stub(url=>url==='/api/session'?json({authenticated:true,mode:'server',token:'tok'}):new Response(null,{status:204}));
 await api.logout();
 assert.equal(calls.find(c=>c.url==='/api/auth/logout')?.token,'tok');
});

test('server mode uses the network offline wording',async()=>{
 globalThis.fetch=(async ()=>{throw new TypeError('fetch failed');}) as typeof fetch;
 await assert.rejects(api.api('/overview'),(e:Error)=>e.message==='无法连接本地服务，请确认已启动');
 api.setServerMode(true);
 await assert.rejects(api.api('/overview'),(e:Error)=>e.message==='无法连接服务，请检查网络后重试');
});

test('unavailable sessions raise 503 with the server detail',async()=>{
 stub(()=>json({authenticated:false,mode:'server',unavailable:{code:'disk_full',detail:'服务器磁盘空间不足，服务暂不可用，请联系管理员。'}}));
 await assert.rejects(api.api('/overview'),(e:InstanceType<typeof api.ApiError>)=>e.status===503&&e.code==='disk_full'&&e.message.startsWith('服务器磁盘'));
});
