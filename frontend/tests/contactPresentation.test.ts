import './support/registerTsx.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import type {Contact} from '../src/types.ts';

// Render real TSX components using the existing Node test runner.

const {ContactCard} = await import('../src/ContactCard.tsx');
const {ContactGroups} = await import('../src/ContactGroups.tsx');
const base: Contact = {id: 'buyer', name: 'Jane Buyer', role: 'Buyer', email: '', url: '', kind: 'procurement', role_status: 'explicit', association_status: 'same_block', source_url: 'https://example.com/team', evidence: 'Jane buys stock', observed_at: '2026-09-22', review_status: 'valid', fields: []};
const renderCard = (contact: Contact) => renderToStaticMarkup(createElement(ContactCard, {contact}));

test('all distinct evidence/page pairs appear and field provenance is preserved', () => {
  const html = renderCard({...base, fields: [{key: 'email', label: '邮箱', value: 'jane@example.com', source_url: 'https://example.com/email', source_label: 'Email source', observed_at: base.observed_at}], sources: [
    {source_url: base.source_url, evidence: base.evidence, observed_at: base.observed_at},
    {source_url: base.source_url, evidence: base.evidence, observed_at: '2026-09-21'},
    {source_url: 'https://example.com/contact', evidence: 'Contact purchasing here', observed_at: base.observed_at},
    {source_url: base.source_url, evidence: 'Responsible for supplier selection', observed_at: base.observed_at},
    {source_url: 'https://example.com/another', evidence: base.evidence, observed_at: base.observed_at},
  ]});
  assert.equal((html.match(/<blockquote>/g) || []).length, 4);
  for (const text of ['Contact purchasing here', 'Responsible for supplier selection', 'https://example.com/contact', 'https://example.com/another', 'https://example.com/email', 'Email source', 'jane@example.com']) assert.ok(html.includes(text), text);
});

test('legacy and empty sources preserve evidence and the missing-evidence fallback', () => {
  for (const sources of [undefined, []]) {
    const html = renderCard({...base, sources});
    assert.ok(html.includes(base.evidence));
    assert.ok(html.includes(base.source_url));
  }
  assert.ok(renderCard({...base, evidence: '', source_url: '', sources: []}).includes('未提供证据片段'));
});

test('people and channels are visible while other contacts start collapsed', () => {
  const html = renderToStaticMarkup(createElement(ContactGroups, {contacts: [base,
    {...base, id: 'department', name: '', role_status: 'department'},
    {...base, id: 'other', name: 'Unknown role', role_status: 'unknown', evidence: ''},
  ]}));
  const beforeOther = html.split('<details class="other-contacts">')[0];
  assert.ok(beforeOther.includes('Jane Buyer'));
  assert.ok(beforeOther.includes('采购部门 / 渠道'));
  assert.ok(beforeOther.includes('企业 / 部门联系方式'));
  assert.ok(!beforeOther.includes('Unknown role'));
  assert.ok(html.includes('Unknown role'));
  assert.doesNotMatch(html, /<details[^>]*\bopen/);
});

test('invalid review states render neither standalone cards nor grouped identities', () => {
  for (const review_status of ['invalid', 'superseded']) {
    const contact = {...base, review_status, adoption_status: 'accepted' as const};
    assert.equal(renderCard(contact), '');
    assert.ok(!renderToStaticMarkup(createElement(ContactGroups, {contacts: [contact]})).includes(base.name));
  }
});

test('conflicting field values and their individual sources remain visible', () => {
  const html = renderCard({...base, fields: [
    {key: 'email', label: '邮箱', value: 'first@example.com', source_url: 'https://example.com/first', source_label: 'First source', observed_at: base.observed_at},
    {key: 'email', label: '邮箱', value: 'second@example.com', source_url: 'https://example.com/second', source_label: 'Second source', observed_at: base.observed_at},
  ]});
  for (const text of ['first@example.com', 'second@example.com', 'https://example.com/first', 'https://example.com/second', 'First source', 'Second source']) assert.ok(html.includes(text), text);
});

test('backend grouped records retain separate cards and counts without frontend merging', () => {
  const html = renderToStaticMarkup(createElement(ContactGroups, {contacts: [base, {...base, id: 'separate-record', record_ids: ['original-2', 'original-3']}]}));
  assert.equal((html.match(/class="contact-card"/g) || []).length, 2);
  assert.ok(html.includes('class="count">2</span>'));
});

test('pending and rejected records render only inside their separate folded groups',()=>{
 const html=renderToStaticMarkup(createElement(ContactGroups,{contacts:[{...base,name:'Pending buyer',review_status:'pending'},{...base,name:'Rejected buyer',review_status:'reject',adoption_status:'excluded'}]}));
 assert.ok(html.includes('待确认联系人'));assert.ok(html.includes('不采用的联系人'));
 assert.ok(!html.split('<details class="other-contacts pending-contacts">')[0].includes('Pending buyer'));
 assert.ok(html.includes('Rejected buyer'));assert.doesNotMatch(html,/<details[^>]*\bopen/);
});
