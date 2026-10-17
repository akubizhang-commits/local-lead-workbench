import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import type {Region,RegionConfig} from '../src/types.ts';

const configs:RegionConfig[]=[
 {country:'US',name:'美国',mode:'county_filter',labels:{state:'州 / 特区',county:'县 / 同级地区（可选）',place:'城市 / 居民点'},
  levels:['country','state','county','place'],attribution:'美国人口普查局 · 2026年1月地区数据 · 50州及华盛顿特区',
  hint:'县筛选可选；跨县城市按整个城市检索，不裁剪市场范围。'},
 {country:'ZA',name:'南非',mode:'municipal_branch',labels:{state:'省',county:'地区 / 大都市自治市',place:'地方自治市'},
  levels:['country','state','county','place'],terminal_subtypes:['metropolitan_municipality'],
  scope:'whole_municipality',attribution:'南非地区数据（测试）',hint:'选择大都市自治市，或先选地区再选地方自治市。'},
];
const zaProvince:Region={id:'ZA-GT',name:'Gauteng',parent_id:'ZA',kind:'state'};
const zaOtherProvince:Region={id:'ZA-WC',name:'Western Cape',parent_id:'ZA',kind:'state'};
const zaDistrict:Region={id:'ZA-GT-D1',name:'Sedibeng',parent_id:'ZA-GT',state_id:'ZA-GT',kind:'county'};
const zaMetro:Region={id:'ZA-GT-M1',name:'City of Johannesburg',official_name:'City of Johannesburg Metropolitan Municipality',
 parent_id:'ZA-GT',state_id:'ZA-GT',kind:'place',subtype:'metropolitan_municipality',county_ids:[]};
const zaLocal:Region={id:'ZA-GT-L1',name:'Emfuleni',official_name:'Emfuleni Local Municipality',
 parent_id:'ZA-GT',state_id:'ZA-GT',kind:'place',subtype:'local_municipality',county_ids:[zaDistrict.id]};

const state = {id:'US-XY',name:'Example State',parent_id:'US',kind:'state'};
const district = {id:'US-DC',name:'District of Columbia',parent_id:'US',kind:'state',subtype:'federal_district'};
const counties = [
 {id:'county-a',name:'North County',parent_id:state.id,state_id:state.id,kind:'county'},
 {id:'county-b',name:'South County',parent_id:state.id,state_id:state.id,kind:'county'},
];
const places = [
 {id:'place-city',name:'Shared',official_name:'Shared city',parent_id:state.id,state_id:state.id,
  kind:'place',subtype:'incorporated_place',county_ids:['county-a','county-b'],geoid:'001'},
 {id:'place-cdp',name:'Shared',official_name:'Shared CDP',parent_id:state.id,state_id:state.id,
  kind:'place',subtype:'cdp',county_ids:['county-b'],geoid:'002'},
];

async function fixture(options:{failCounties?:boolean;failZACounties?:boolean;delayStateReload?:boolean;extraPlaces?:Region[];configs?:RegionConfig[]}={}) {
 const h=await setupDOM();
 const savedFormData=globalThis.FormData;
 globalThis.FormData=h.dom.window.FormData as typeof FormData;
 const requests:string[]=[],submitted:Record<string,unknown>[]=[];
 const catalogPlaces:Region[]=[...places,zaMetro,zaLocal,...(options.extraPlaces||[])];
 let stateReads=0,releaseState:()=>void=()=>{};
 globalThis.fetch=async(input,init)=>{
  const path=String(input);requests.push(path);
  if(path==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
  if(path==='/api/region-configs')return new Response(JSON.stringify(options.configs||configs));
  if(path==='/api/tasks'&&init?.method==='POST'){
   submitted.push(JSON.parse(String(init.body)));
   return new Response(JSON.stringify({id:'synthetic-task'}));
  }
  assert.equal(init?.method||'GET','GET');
  const url=new URL(path,'http://fixture.invalid');
  assert.equal(url.pathname,'/api/regions');
  const parent=url.searchParams.get('parent_id'),kind=url.searchParams.get('kind');
  let result:unknown=[];
  if(!parent)result=[{id:'US',name:'United States',parent_id:null,kind:'country'},{id:'ZA',name:'South Africa',parent_id:null,kind:'country'}];
  else if(parent==='US'){
   if(options.delayStateReload&&++stateReads>1)await new Promise<void>(resolve=>{releaseState=resolve;});
   result=[state,district];
  }
  else if(parent===state.id&&kind==='county'){
   if(options.failCounties)return new Response(JSON.stringify({detail:'County catalog unavailable'}),{status:503});
   result=counties;
  } else if(parent===state.id&&kind==='place'){
   const county=url.searchParams.get('county_id');
   result=catalogPlaces.filter(place=>place.state_id===state.id&&(!county||place.county_ids?.includes(county)));
  } else if(parent==='ZA')result=[zaProvince,zaOtherProvince];
  else if(parent===zaProvince.id&&kind==='county'){
   if(options.failZACounties)return new Response(JSON.stringify({detail:'District catalog unavailable'}),{status:503});
   result=[zaDistrict];
  }
  else if(parent===zaProvince.id&&kind==='place'){
   const county=url.searchParams.get('county_id');
   result=[zaMetro,zaLocal].filter(place=>!county||place.county_ids?.includes(county));
  }
  return new Response(JSON.stringify(result));
 };
 const flush=()=>h.act(async()=>{await new Promise(resolve=>setTimeout(resolve,15));});
 const input=(name:string)=>document.querySelector<HTMLInputElement|HTMLSelectElement>(`input[name="${name}"],select[name="${name}"]`)!;
 const fill=async(name:string,value:string)=>{
  if(name==='country'&&input(name)?.tagName==='SELECT'
     && !Array.from((input(name) as HTMLSelectElement).options).some(option=>option.value===value)){
   await h.act(async()=>{
    input(name).value='__other';input(name).dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));
   });
  }
  assert.ok(input(name),`Missing ${name} input`);
  await h.act(async()=>{
   if(input(name).tagName==='SELECT'){
    input(name).value=value;
    input(name).dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));
    return;
   }
   Object.getOwnPropertyDescriptor(h.dom.window.HTMLInputElement.prototype,'value')!.set!.call(input(name),value);
   input(name).dispatchEvent(new h.dom.window.Event('input',{bubbles:true}));
  });
  await flush();
 };
 const chooseCounty=async(value:string)=>{
  const select=document.querySelector<HTMLSelectElement>('select[name="county_id"]');
  assert.ok(select,'County chooser must be available');
  await h.act(async()=>{select.value=value;select.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});
  await flush();
 };
 const choosePlace=async(id:string)=>{
  const region=catalogPlaces.find(place=>place.id===id)!;
  if(input('city')?.tagName==='SELECT'){await fill('city',id);return;}
  const list=document.getElementById(input('city').getAttribute('list')!);
  const option=[...(list?.querySelectorAll('option')||[])].find(o=>
   `${o.value} ${o.textContent}`.includes(region.official_name));
  assert.ok(option,`Missing official place label ${region.official_name}`);
  await fill('city',option.value);
 };
 const submit=async(expectSaved=true)=>{
  assert.equal(document.querySelector('input[name="name"]'),null);
  await h.act(async()=>document.querySelector('form')!.dispatchEvent(
   new h.dom.window.Event('submit',{bubbles:true,cancelable:true})));
  await flush();
  assert.equal(submitted.length,expectSaved?1:0);
  return submitted[0];
 };
 const {TaskForm}=await import('../src/forms.tsx');
 await h.act(async()=>h.root.render(createElement(TaskForm,{onDone:()=>{},onCancel:()=>{}})));
 await flush();
 return {...h,requests,submitted,input,fill,chooseCounty,choosePlace,submit,
  async cleanup(){try{releaseState();await h.cleanup();}finally{globalThis.FormData=savedFormData;}}};
}

test('new markets default to all social platforms and basic profiles only',async()=>{
 const h=await fixture();
 try{
  const checkbox=document.querySelector<HTMLInputElement>('input[name="auto_social"]');
  assert.ok(checkbox);assert.equal(checkbox.checked,true);
  const body=await h.submit();
  assert.equal(body.auto_social,true);
  assert.deepEqual(body.social_platforms,['facebook','instagram','x']);
  assert.equal(body.social_read_posts,false);
 }finally{await h.cleanup();}
});

test('market creation submits selected social platforms, optional posts, and explicit opt out',async()=>{
 const h=await fixture();
 try{
  const checkbox=(selector:string)=>{
   const input=document.querySelector<HTMLInputElement>(selector);assert.ok(input);return input;
  };
  await h.act(async()=>{
   checkbox('input[name="social_platforms"][value="instagram"]').click();
   checkbox('input[name="social_read_posts"]').click();
  });
  let body=await h.submit();
  assert.deepEqual(body.social_platforms,['facebook','x']);assert.equal(body.social_read_posts,true);
  h.submitted.length=0;
  await h.act(async()=>checkbox('input[name="auto_social"]').click());
  body=await h.submit();
  assert.equal(body.auto_social,false);
  assert.deepEqual(body.social_platforms,['facebook','x']);
 }finally{await h.cleanup();}
});

test('an enabled social stage requires a platform without discarding other market input',async()=>{
 const h=await fixture();
 try{
  const inputs=document.querySelectorAll<HTMLInputElement>('input[name="social_platforms"]');
  assert.equal(inputs.length,3);
  await h.act(async()=>inputs.forEach(input=>input.click()));
  await h.submit(false);
  assert.match(document.body.textContent!,/至少选择一个社媒平台/);
  assert.equal(h.submitted.length,0);
 }finally{await h.cleanup();}
});

test('statewide places use the state endpoint and distinguish official city/CDP choices',async()=>{
 const h=await fixture();
 try{
  await h.fill('state',state.name);
  assert.ok(h.requests.includes(`/api/regions?parent_id=${state.id}&kind=county`));
  assert.ok(h.requests.includes(`/api/regions?parent_id=${state.id}&kind=place`));
  assert.match(document.body.textContent!,/全州城市（不按县筛选）/);
  const list=document.getElementById(h.input('city').getAttribute('list')!)!;
  assert.match(list.textContent!+list.innerHTML,/Shared CDP/);
  assert.match(list.textContent!+list.innerHTML,/人口普查|CDP/);
  await h.choosePlace('place-cdp');
  const body=await h.submit();
  assert.equal(body.country,'US');assert.equal(body.state,state.name);assert.equal(body.city,'Shared');
  assert.equal(body.state_id,state.id);assert.equal(body.place_id,'place-cdp');
  assert.equal(body.county_id,undefined);assert.equal(body.county,undefined);
 }finally{await h.cleanup();}
});

test('geography terminology includes county equivalents and DC without changing canonical values',async()=>{
 const h=await fixture();
 try{
  const stateList=document.getElementById(h.input('state').getAttribute('list')!)!;
  const dc=[...stateList.querySelectorAll('option')].find(option=>option.value===district.name);
  assert.ok(dc);
  assert.match(dc.textContent!,/华盛顿特区/);
  await h.fill('state',state.name);
  const placeList=document.getElementById(h.input('city').getAttribute('list')!)!;
  assert.match(placeList.innerHTML,/建制市镇/);
  assert.match(document.body.textContent!,/城市 \/ 居民点/);
  assert.match(document.body.textContent!,/县 \/ 同级地区（可选）/);
  assert.doesNotMatch(document.body.textContent!+placeList.innerHTML,/城市 \/ 地方|建制城市/);
  await h.fill('state',district.name);await h.fill('city','Washington');
  const body=await h.submit();
  assert.equal(body.state,district.name);
  assert.equal(body.state_id,district.id);
 }finally{await h.cleanup();}
});

test('optional county filters places without making a cross-county place its child',async()=>{
 const h=await fixture();
 try{
  await h.fill('state',state.name);
  await h.chooseCounty('county-a');
  await h.choosePlace('place-city');
  await h.chooseCounty('county-b');
  assert.equal(h.input('city').value,'');
  assert.ok(h.requests.includes(`/api/regions?parent_id=${state.id}&kind=place&county_id=county-b`));
  assert.ok(!h.requests.some(path=>path.includes('parent_id=county-')));
  await h.choosePlace('place-city');
  const body=await h.submit();
  assert.equal(body.place_id,'place-city');assert.equal(body.county_id,'county-b');
  assert.equal(body.county,'South County');assert.equal(body.city,'Shared');
  assert.match(document.body.textContent!,/跨.*县/);
  assert.match(document.body.textContent!,/筛选.*不.*裁剪|不.*裁剪/);
 }finally{await h.cleanup();}
});

test('manual edits remove only nonmatching geographic IDs and clear descendants',async()=>{
 const h=await fixture();
 try{
  await h.fill('state',state.name);await h.chooseCounty('county-a');await h.choosePlace('place-city');
  await h.fill('city','Unlisted Village');
  await h.submit(false);
  assert.match(document.body.textContent!,/按县筛选时，请从城市列表中选择有效城市|请选择所属地区内的有效城市或自治市/);
  assert.equal(h.input('city').value,'Unlisted Village','Validation must retain the entered text');
  await h.chooseCounty('');
  await h.fill('city','Unlisted Village');
  const body=await h.submit();
  assert.equal(body.place_id,undefined);assert.equal(body.city,'Unlisted Village');
  assert.equal(body.state_id,state.id);assert.equal(body.county_id,undefined);
  await h.fill('state','Custom Province');
  assert.equal(h.input('city').value,'');
  assert.equal(document.querySelector<HTMLSelectElement>('select[name=county_id]')?.value||'','');
  await h.fill('city','Custom City');
  await h.fill('country','mx');
  assert.equal(h.input('country').value,'MX');
  assert.equal(h.input('state').value,'');assert.equal(h.input('city').value,'');
 }finally{await h.cleanup();}
});

test('free country/state/city text submits without fabricated region identifiers',async()=>{
 const h=await fixture();
 try{
  await h.fill('country','ZZ');await h.fill('state','Custom Province');await h.fill('city','Custom City');
  const body=await h.submit();
  assert.equal(body.country,'ZZ');assert.equal(body.state,'Custom Province');assert.equal(body.city,'Custom City');
  for(const key of ['state_id','county_id','place_id','county'])assert.equal(body[key],undefined);
  assert.match(document.body.textContent!,/未验证/);
 }finally{await h.cleanup();}
});

test('ambiguous plain place name never picks an arbitrary city or CDP identifier',async()=>{
 const h=await fixture();
 try{
  await h.fill('state',state.name);await h.fill('city','Shared');
  const body=await h.submit();
  assert.equal(body.city,'Shared');assert.equal(body.place_id,undefined);
 }finally{await h.cleanup();}
});

test('county lookup failure remains visible while statewide place selection still works',async()=>{
 const h=await fixture({failCounties:true});
 try{
  await h.fill('state',state.name);
  assert.match(document.body.textContent!,/County catalog unavailable|县.*失败|县.*不可用/);
  await h.choosePlace('place-city');
  const body=await h.submit();
  assert.equal(body.place_id,'place-city');assert.equal(body.county_id,undefined);
 }finally{await h.cleanup();}
});

test('retrying a failed catalog preserves an already matched canonical place while requests load',async()=>{
 const h=await fixture({failCounties:true,delayStateReload:true});
 try{
  await h.fill('state',state.name);await h.choosePlace('place-city');
  const retry=[...document.querySelectorAll('button')].find(button=>button.textContent==='重试地区建议');
  assert.ok(retry);
  await h.act(async()=>retry.click());
  const body=await h.submit();
  assert.equal(body.city,'Shared');
  assert.equal(body.state_id,state.id);
  assert.equal(body.place_id,'place-city');
 }finally{await h.cleanup();}
});

for(const mode of ['different-counties','same-county','county-unavailable','missing-geoid'] as const) {
 test(`identical official place names remain selectable: ${mode}`,async()=>{
  const duplicate=(id:string,countyId:string,geoid?:string):Region=>({
   id,name:'Springfield',official_name:'Springfield CDP',subtype:'cdp',kind:'place',
   parent_id:state.id,state_id:state.id,county_ids:[countyId],geoid,
  });
  const a=duplicate('springfield-a','county-a',mode==='missing-geoid'?undefined:'99001');
  const b=duplicate('springfield-b',mode==='different-counties'?'county-b':'county-a',
   mode==='missing-geoid'?undefined:'99002');
  const h=await fixture({extraPlaces:[a,b],failCounties:mode==='county-unavailable'});
  try{
   await h.fill('state',state.name);
   const list=document.getElementById(h.input('city').getAttribute('list')!)!;
   const options=[...list.querySelectorAll('option')].filter(option=>option.value.includes('Springfield CDP'));
   assert.equal(options.length,2);
   assert.notEqual(options[0].value,options[1].value,'Identical suggestions cannot select distinct PlaceIDs');
   if(mode==='different-counties'){
    assert.match(options[0].value,/North County/);assert.match(options[1].value,/South County/);
    assert.doesNotMatch(options.map(option=>option.value).join(' '),/99001|99002|springfield-/);
   }else{
    assert.match(options[1].value,mode==='missing-geoid'?/springfield-b/:/99002/);
   }
   const normal=[...list.querySelectorAll('option')].find(option=>option.value.includes('Shared city'))!;
   assert.doesNotMatch(normal.value,/001|place-city|North County/,'Ordinary suggestions must not gain raw identifiers');
   await h.fill('city',options[1].value);
   const body=await h.submit();
   assert.equal(body.city,'Springfield');assert.equal(body.place_id,b.id);
  }finally{await h.cleanup();}
 });
}

test('country dropdown uses API configuration names and codes',async()=>{
 const h=await fixture();
 try{
  const countries=document.querySelector<HTMLSelectElement>('select[name=country]');
  assert.ok(countries);
  assert.match(countries.textContent!,/美国.*US/);
  assert.match(countries.textContent!,/南非.*ZA/);
  assert.ok(h.requests.includes('/api/region-configs'));
 }finally{await h.cleanup();}
});

test('ZA metro ends at three controls and submits municipality identity without county',async()=>{
 const h=await fixture();
 try{
  await h.fill('country','ZA');await h.fill('state',zaProvince.name);
  await h.fill('municipal_branch',zaMetro.id);
  assert.equal(document.querySelector('input[name=city],select[name=city]'),null);
  assert.match(document.body.textContent!,/大都市自治市/);
  assert.doesNotMatch(document.body.textContent!,/美国人口普查局|建制市镇|城市 \/ 居民点/);
  const body=await h.submit();
  assert.equal(body.country,'ZA');assert.equal(body.state,'Gauteng');
  assert.equal(body.state_id,zaProvince.id);assert.equal(body.place_id,zaMetro.id);
  assert.equal(body.city,zaMetro.name);assert.equal(body.county_id,undefined);assert.equal(body.county,undefined);
 }finally{await h.cleanup();}
});

test('ZA district requires and submits a matching local municipality as the fourth level',async()=>{
 const h=await fixture();
 try{
  await h.fill('country','ZA');await h.fill('state',zaProvince.name);
  await h.fill('municipal_branch',zaDistrict.id);
  const locals=document.querySelector<HTMLSelectElement>('select[name=city]');
  assert.ok(locals);assert.equal(locals.required,true);
  assert.match(document.body.textContent!,/地方自治市/);
  assert.ok(!Array.from(locals.options).some(option=>option.value===zaMetro.id));
  assert.ok(h.requests.includes(`/api/regions?parent_id=${zaProvince.id}&kind=place&county_id=${zaDistrict.id}`));
  await h.submit(false);
  await h.choosePlace(zaLocal.id);
  const body=await h.submit();
  assert.equal(body.place_id,zaLocal.id);assert.equal(body.city,zaLocal.name);
  assert.equal(body.county_id,zaDistrict.id);assert.equal(body.county,zaDistrict.name);
 }finally{await h.cleanup();}
});

test('ZA province alone cannot create a task, including a direct submit event',async()=>{
 const h=await fixture();
 try{
  await h.fill('country','ZA');await h.fill('state',zaProvince.name);
  const branch=document.querySelector<HTMLSelectElement>('select[name=municipal_branch]');
  assert.ok(branch);assert.equal(branch.required,true);assert.equal(branch.value,'');
  await h.submit(false);
  assert.match(document.body.textContent!,/请选择.*地区.*大都市自治市/);
 }finally{await h.cleanup();}
});

test('ZA branch, province and country switches cannot retain previous municipality IDs',async()=>{
 const h=await fixture();
 try{
  await h.fill('country','ZA');await h.fill('state',zaProvince.name);
  await h.fill('municipal_branch',zaDistrict.id);await h.choosePlace(zaLocal.id);
  await h.fill('municipal_branch',zaMetro.id);
  assert.equal(document.querySelector('input[name=county_id]'),null);
  assert.equal(document.querySelector<HTMLInputElement>('input[name=place_id]')?.value,zaMetro.id);
  await h.fill('municipal_branch',zaDistrict.id);
  assert.equal(h.input('city').value,'');
  assert.equal(document.querySelector('input[name=place_id]'),null);
  await h.fill('municipal_branch',zaMetro.id);
  await h.fill('state',zaOtherProvince.name);
  assert.equal(h.input('municipal_branch').value,'');
  assert.equal(document.querySelector('input[name=place_id]'),null);
  await h.fill('country','US');
  assert.equal(h.input('state').value,'');
  assert.equal(document.querySelector('select[name=municipal_branch]'),null);
  assert.ok(document.querySelector('select[name=county_id]'));
 }finally{await h.cleanup();}
});

test('server labels and hints override built-in display text',async()=>{
 const h=await fixture({configs:configs.map(config=>config.country==='ZA'?{
  ...config,labels:{state:'一级行政区',county:'自治分支',place:'地方行政单元'},
  attribution:'Fixture authoritative attribution',hint:'Fixture authoritative hint',
 }:config)});
 try{
  await h.fill('country','ZA');await h.fill('state',zaProvince.name);
  await h.fill('municipal_branch',zaDistrict.id);
  for(const expected of ['一级行政区','自治分支','地方行政单元','Fixture authoritative attribution','Fixture authoritative hint']){
   assert.ok(document.body.textContent!.includes(expected),expected);
  }
 }finally{await h.cleanup();}
});

test('terminal subtype and mode come from configuration rather than the country code',async()=>{
 const h=await fixture({configs:configs.map(config=>config.country==='US'?{
  ...config,mode:'municipal_branch',terminal_subtypes:['incorporated_place'],
  labels:{state:'Configured province',county:'Configured branch',place:'Configured locality'},
 }:config)});
 try{
  await h.fill('state',state.name);
  assert.ok(document.querySelector('select[name=municipal_branch]'));
  await h.fill('municipal_branch','place-city');
  assert.equal(document.querySelector('input[name=city],select[name=city]'),null);
  const body=await h.submit();
  assert.equal(body.place_id,'place-city');assert.equal(body.county_id,undefined);
 }finally{await h.cleanup();}
});

test('a district catalogue failure does not disable already loaded metro choices',async()=>{
 const h=await fixture({failZACounties:true});
 try{
  await h.fill('country','ZA');await h.fill('state',zaProvince.name);
  assert.match(document.body.textContent!,/District catalog unavailable/);
  assert.equal(document.querySelector<HTMLSelectElement>('select[name=municipal_branch]')!.disabled,false);
  await h.fill('municipal_branch',zaMetro.id);
  const body=await h.submit();
  assert.equal(body.place_id,zaMetro.id);assert.equal(body.county_id,undefined);
 }finally{await h.cleanup();}
});

test('three-level county_filter configuration omits county controls, requests and payload',async()=>{
 const h=await fixture({configs:configs.map(config=>config.country==='US'?{
  ...config,levels:['country','state','place'],hint:'按州选择居民点。',
 }:config)});
 try{
  await h.fill('state',state.name);
  assert.equal(!!document.querySelector('select[name=county_id]'),false,'County control must follow levels');
  assert.equal(!!document.querySelector('select[name=municipal_branch]'),false);
  assert.ok(!h.requests.some(path=>path.includes('kind=county')));
  assert.ok(h.requests.includes(`/api/regions?parent_id=${state.id}&kind=place`));
  await h.choosePlace('place-city');
  const body=await h.submit();
  assert.equal(body.state_id,state.id);assert.equal(body.place_id,'place-city');
  assert.equal(body.city,'Shared');
  assert.equal(body.county_id,undefined);assert.equal(body.county,undefined);
 }finally{await h.cleanup();}
});
