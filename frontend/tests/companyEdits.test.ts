import test from 'node:test';import assert from 'node:assert/strict';
import {batchStatusPayload,businessChoices} from '../src/companyEdits.ts';
test('batch status sends selected IDs and captured revisions only',()=>{
 const selected=[{id:'a',revision:4,name:'Private note'},{id:'b',revision:9,name:'Other'}];
 assert.deepEqual(batchStatusPayload(selected,'verified'),{companies:[{id:'a',revision:4},{id:'b',revision:9}],status:'verified'});
 assert.throws(()=>batchStatusPayload([],'verified'));
 assert.throws(()=>batchStatusPayload(selected,'bogus'));
});
test('business editor preserves existing categories outside default choices',()=>{assert.ok(businessChoices(['wholesale','custom']).includes('custom'));assert.equal(businessChoices(['wholesaler']).filter(x=>x==='wholesaler').length,1);});
