import test from 'node:test';
import assert from 'node:assert/strict';
import { filtersQuery, safeUrl, runActions } from '../src/helpers.ts';
test('export query retains Chinese, ampersands and selected filters', () => {
 const query = new URLSearchParams(filtersQuery({q:'服装 & 进口',status:'verified',type:'wholesale'}));
 assert.equal(query.get('q'),'服装 & 进口'); assert.equal(query.get('status'),'verified'); assert.equal(query.get('type'),'wholesale');
});
test('external links reject executable and relative URLs', () => {
 assert.equal(safeUrl('javascript:alert(1)'),undefined); assert.equal(safeUrl('/api/backups'),undefined); assert.equal(safeUrl('https://example.org/team'),'https://example.org/team');
});
test('manual recovery is offered for blocked and failed runs but not completed runs', () => {
 assert.deepEqual(runActions('completed'),[]); assert.deepEqual(runActions('blocked'),['resume','cancel']); assert.deepEqual(runActions('failed'),['resume']); assert.deepEqual(runActions('paused'),['resume','cancel']); assert.deepEqual(runActions('running'),['pause','cancel']);
});

test('market and run filters survive pagination without leaking pagination into exports', () => {
 const filters={q:'服装 & 进口',status:'candidate',type:'wholesale',market_id:'市场/a & b',run_id:'run/2',segment:'changed'};
 const query=new URLSearchParams(filtersQuery(filters,3));
 assert.equal(query.get('page'),'3'); assert.equal(query.get('page_size'),'50');
 for(const [key,value] of Object.entries(filters)) assert.equal(query.get(key),value);
 const exported=new URLSearchParams(filtersQuery(filters));
 assert.equal(exported.has('page'),false);assert.equal(exported.has('page_size'),false);
 assert.equal(exported.get('market_id'),filters.market_id);assert.equal(exported.get('run_id'),'run/2');assert.equal(exported.get('segment'),'changed');
});
test('empty optional market filters are omitted and pagination starts at one', () => {
 const query=new URLSearchParams(filtersQuery({q:'',status:'',type:'',market_id:'',run_id:'',segment:''},1));
 assert.deepEqual([...query.entries()],[['page','1'],['page_size','50']]);
});

test('capability-exhausted runs never offer ineffective resume; retryable runs still do',()=>{
 const exhausted={capability_exhausted:true,retryable:false,unresolved:['密集区域未覆盖']};
 assert.deepEqual(runActions('partial',exhausted),[]);
 assert.deepEqual(runActions('blocked',exhausted),['cancel']);
 assert.deepEqual(runActions('paused',exhausted),['cancel']);
 assert.deepEqual(runActions('partial',{...exhausted,retryable:true}),['resume']);
 assert.deepEqual(runActions('partial',{capability_exhausted:false,retryable:false}),['resume']);
});
test('array filters repeat their key and empty arrays disappear', () => {
 const query = new URLSearchParams(filtersQuery({q:'',status:'',type:'',category:['Roofing supply store','Metal supplier'],excluded:'only'},1));
 assert.deepEqual(query.getAll('category'), ['Roofing supply store','Metal supplier']);
 assert.equal(query.get('excluded'), 'only');
 assert.equal(filtersQuery({q:'',status:'',type:'',category:[],excluded:''}), '');
});
test('export URL carries layout, format and every list filter', async () => {
 const {exportUrl} = await import('../src/helpers.ts');
 const url = new URL(exportUrl({q:'roof',status:'',type:'',market_id:'m1',category:['Metal supplier'],excluded:'include',best_tier:'named_role'},'csv','entries'),'http://x');
 assert.equal(url.pathname,'/exports');
 assert.deepEqual(Object.fromEntries(url.searchParams),{q:'roof',market_id:'m1',category:'Metal supplier',excluded:'include',best_tier:'named_role',format:'csv',layout:'entries'});
});
