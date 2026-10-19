import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {ActualCityCell,ActualCityLine}=await import('../src/ActualCity.tsx');
const {parseRoute,routeUrl,defaultFilters}=await import('../src/navigation.ts');
const {exportBody}=await import('../src/ExportJobs.tsx');

const near={city:'San Jose',country:'US',actual_city:'Campbell',actual_city_basis:'address' as const,nearby:true};

test('list cell: actual city with a nearby badge, or the target city when unparsed',()=>{
 const html=renderToStaticMarkup(createElement(ActualCityCell,{company:near}));
 assert.match(html,/Campbell \/ US/);
 assert.match(html,/title="地址在 Campbell，不在目标城市 San Jose"[^>]*>邻近城市</);
 const same=renderToStaticMarkup(createElement(ActualCityCell,{company:{...near,actual_city:'San Jose',nearby:false}}));
 assert.doesNotMatch(same,/邻近城市/);
 const target=renderToStaticMarkup(createElement(ActualCityCell,{company:{...near,actual_city:'San Jose',actual_city_basis:'target',nearby:false}}));
 assert.match(target,/San Jose \/ US/);
 assert.match(target,/按目标城市显示（地址未解析）/);
});

test('detail line: parsed and unparsed wording',()=>{
 const company={id:'c',name:'x',...near} as never;
 assert.match(renderToStaticMarkup(createElement(ActualCityLine,{company})),/实际城市：Campbell（按地址解析） · 目标城市：San Jose.*邻近城市/);
 const unparsed={...near,id:'c',actual_city:'San Jose',actual_city_basis:'target',nearby:false} as never;
 assert.match(renderToStaticMarkup(createElement(ActualCityLine,{company:unparsed})),/实际城市：未能从地址解析，暂按目标城市 San Jose 显示/);
});

test('city filters go into the URL and the export request',()=>{
 const route={view:'companies' as const,selected:null,page:1,filters:{...defaultFilters(),market_id:'m1',actual_city:'Campbell',nearby:'only' as const}};
 const url=routeUrl(route);
 assert.match(url,/actual_city=Campbell/);
 assert.match(url,/nearby=only/);
 const parsed=parseRoute(url);
 assert.equal(parsed.filters.actual_city,'Campbell');
 assert.equal(parsed.filters.nearby,'only');
 assert.equal(parseRoute('/?view=companies&nearby=far').filters.nearby,'');
 const body=exportBody(route.filters,'csv','entries');
 assert.equal(body.actual_city,'Campbell');
 assert.equal(body.nearby,'only');
});

test('market summary shows the nearby count and opens the filtered library',async()=>{
 const h=await setupDOM();
 try {
  const {MarketSummary}=await import('../src/MarketSummary.tsx');
  let opened=0;
  const market={id:'m',name:'Market',country:'US',state:'California',city:'San Jose',keywords:['x'],company_types:[],
   auto_enrich:true,created_at:'2026-10-15T00:00:00Z',latest_run:null,
   market_stats:{total:3,new:3,existing:0,changed:0,not_seen:0,nearby:1}};
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market,onNearby:()=>{opened++;}} as never)));
  const button=[...document.querySelectorAll('button')].find(b=>b.textContent==='邻近城市 1 家')!;
  assert.ok(button);
  await h.act(async()=>button.click());
  assert.equal(opened,1);
 } finally {await h.cleanup();}
});
