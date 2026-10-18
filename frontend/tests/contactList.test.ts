import test from 'node:test';
import assert from 'node:assert/strict';
import {listContacts,contactTitle,contactChannels} from '../src/contactList.ts';
import type {Contact} from '../src/types';
const c={id:'a',name:'',email:'info@example.com',kind:'general',role_status:'unknown',review_status:'auto_recorded',fields:[]} as Contact;
test('unified list puts buyers first and keeps irrelevant and pending separate',()=>{
 const buyer={...c,id:'b',name:'Buyer',kind:'procurement',role_status:'explicit'};
 const jobs={...c,id:'j',outreach_category:'irrelevant'};
 const pending={...c,id:'p',review_status:'pending'};
 const groups=listContacts([c,jobs,pending,buyer]);
 assert.deepEqual(groups.active.map(c=>c.id),['b','a']);assert.equal(groups.hidden.length,1);assert.equal(groups.pending.length,1);
 assert.equal(contactTitle(c),'info@example.com');assert.equal(contactChannels(c),'邮箱');
});

test('a navigation-only contact page is supplementary, real channels remain',()=>{
 const page={...c,id:'url',email:'',url:'https://example.com/contact',kind:'contact_form'};
 const result=listContacts([page,c]);
 assert.deepEqual(result.active.map(c=>c.id),['a']);assert.deepEqual(result.supplementary.map(c=>c.id),['url']);
 assert.equal(contactChannels(page),'官网联系页面');
});
test('relation shows the backend tier; a name-style mailbox is not a generic company entry',async()=>{
 const {contactRelation}=await import('../src/contactList.ts');
 const named={...c,email:'christian.arredondo@example.com',association_status:'company_only',tier:'named_unknown',tier_label:'具名·岗位未明确'} as Contact;
 assert.equal(contactRelation(named),'具名·岗位未明确');
 assert.notEqual(contactRelation(named),'企业通用入口');
});
