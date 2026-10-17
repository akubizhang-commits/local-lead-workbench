import test from 'node:test';
import assert from 'node:assert/strict';
import {groupContacts} from '../src/groupContacts.ts';

test('named buyers are separated from procurement departments and unnamed channels', () => {
  const buyer = {name: 'Jane', kind: 'procurement', role_status: 'explicit'};
  const department = {name: 'Purchasing', kind: 'procurement', role_status: 'department'};
  const channel = {name: '  ', kind: 'procurement', role_status: 'explicit'};
  const other = ['unknown', 'historical'].map(role_status => ({name: 'Other', kind: 'procurement', role_status, evidence: ''}));
  other.push({name: 'Sales', kind: 'general', role_status: 'explicit', evidence: ''});
  assert.deepEqual(groupContacts([buyer, department, channel, ...other]), {irrelevant: [], procurement: [buyer], channels: [department, channel], other, excluded: [], pending: []});
  assert.deepEqual(groupContacts([]), {irrelevant: [], procurement: [], channels: [], other: [], excluded: [], pending: []});
});

test('invalid review states are hidden regardless of adoption status', () => {
  for (const review_status of ['invalid', 'superseded']) {
    for (const adoption_status of [undefined, 'accepted', 'excluded']) {
      assert.deepEqual(groupContacts([{name: 'Hidden', kind: 'procurement', role_status: 'explicit', review_status, adoption_status}]), {irrelevant: [], procurement: [], channels: [], other: [], excluded: [], pending: []});
    }
  }
});

test('valid excluded contacts stay separate and accepted buyers remain visible', () => {
  const excluded = {name: 'Excluded', kind: 'procurement', role_status: 'explicit', adoption_status: 'excluded', review_status: 'pending'};
  const adopted = {...excluded, name: 'Buyer', adoption_status: 'accepted', review_status: 'valid'};
  assert.deepEqual(groupContacts([excluded, adopted]), {irrelevant: [], procurement: [adopted], channels: [], other: [], excluded: [excluded], pending: []});
});

test('pending evidence is never usable; adoption exclusion takes precedence',()=>{
 const pending={name:'Waiting',kind:'procurement',role_status:'explicit',review_status:'pending',adoption_status:'accepted'};
 const excluded={...pending,name:'Excluded',adoption_status:'excluded'};
 const rejected={...pending,name:'Legacy',review_status:'reject',adoption_status:undefined};
 assert.deepEqual(groupContacts([pending,excluded,rejected]),{irrelevant: [], procurement:[],channels:[],other:[],pending:[pending],excluded:[excluded,rejected]});
});

 test('recruitment is retained outside outreach groups',()=>{
 const job={name:'Jobs',kind:'general',role_status:'unknown',outreach_category:'irrelevant'};
 const unknown={name:'Info',kind:'general',role_status:'unknown',outreach_category:'email'};
 const result=groupContacts([job,unknown]);assert.deepEqual(result.irrelevant,[job]);assert.deepEqual(result.other,[unknown]);
 });
