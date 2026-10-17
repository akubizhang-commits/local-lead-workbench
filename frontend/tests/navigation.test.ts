import test from 'node:test';
import assert from 'node:assert/strict';
import {parseRoute,routeUrl,defaultFilters,updateListFilters} from '../src/navigation.ts';
import {filtersQuery} from '../src/helpers.ts';

test('detail refresh and returning to list retain the entire list context',()=>{
 const route={view:'detail' as const,selected:'企业/a & b',page:3,filters:{...defaultFilters(),q:'采购 & 公司',market_id:'market/1',run_id:'round2',segment:'changed',status:'verified',type:'wholesaler',contact:'procurement' as const,sort:'email' as const}};
 assert.deepEqual(parseRoute(routeUrl(route)),route);
 const list={...parseRoute(routeUrl(route)),view:'companies' as const,selected:null};
 assert.deepEqual(parseRoute(routeUrl(list)),list);
 const exportQuery=new URLSearchParams(filtersQuery(list.filters));
 assert.equal(exportQuery.get('contact'),'procurement');assert.equal(exportQuery.get('sort'),'email');assert.equal(exportQuery.has('page'),false);
});
test('invalid route and pagination values fall back safely',()=>{
 const route=parseRoute('/?view=evil&company=a&page=-9&contact=bad&sort=bad');
 assert.equal(route.view,'tasks');assert.equal(route.page,1);assert.equal(route.filters.contact,'');assert.equal(route.filters.sort,'newest');
 assert.equal(parseRoute('/?view=detail').view,'companies');
 assert.equal(parseRoute('/?view=companies&page=Infinity').page,1);
});
test('new filter retains other constraints but resets page',()=>{
 const route=parseRoute('/?view=companies&page=4&market_id=m&contact=email&sort=updated');
 const next=updateListFilters(route,{status:'verified'});
 assert.equal(next.page,1);assert.equal(next.filters.market_id,'m');assert.equal(next.filters.contact,'email');assert.equal(next.filters.sort,'updated');
});

test('account detail route retains company and list context',()=>{
 const route=parseRoute('http://localhost/?view=detail&company=c1&account=a1&page=3&sort=email');
 assert.equal(route.account,'a1');assert.equal(route.selected,'c1');
 assert.match(routeUrl(route),/account=a1/);
 assert.doesNotMatch(routeUrl({...route,view:'companies'}),/account=/);
});

test('task refresh detail survives reload with list context',()=>{
 const route=parseRoute('/?view=tasks&task=market1&run=round1&item=item1&page=2&activityStatus=failed');
 assert.equal(route.task,'market1');assert.equal(route.item,'item1');
 assert.deepEqual(parseRoute(routeUrl(route)),route);
 assert.doesNotMatch(routeUrl({...route,view:'companies'}),/task=|item=|activityStatus=/);
});

test('refresh detail back retains the original page, status and selected round',async()=>{
 const {openRefreshDetail,backFromRefreshDetail}=await import('../src/navigation.ts');
 for(const run of [undefined,'round2']){
  const list={...parseRoute('/?view=tasks&task=m&page=3&activityStatus=partial'),run};
  const detail=openRefreshDetail(list,{run_id:'round2',id:'work2'});
  const restored=backFromRefreshDetail(parseRoute(routeUrl(detail)));
  assert.equal(restored.page,3);assert.equal(restored.run,run);assert.equal(restored.activityStatus,'partial');assert.equal(restored.item,undefined);
 }
 assert.equal(parseRoute('/?view=tasks&task=m&item=invalid').item,undefined);
 assert.equal(parseRoute('/?view=tasks&task=m&activityStatus=invalid').activityStatus,undefined);
});
