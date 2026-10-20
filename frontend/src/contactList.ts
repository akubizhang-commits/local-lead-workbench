import type {Contact} from './types';
import {groupContacts} from './groupContacts.ts';

export function listContacts(contacts:Contact[]){
 const groups=groupContacts(contacts);
 const supplementary=groups.other.filter(c=>!c.email&&c.url&&!(c.fields||[]).some(f=>['email','phone','whatsapp'].includes(f.key)));
 return {active:[...groups.procurement,...groups.channels,...groups.other.filter(c=>!supplementary.includes(c))],supplementary,pending:groups.pending,hidden:[...groups.irrelevant,...groups.excluded]};
}
export function contactTitle(contact:Contact){
 return contact.name?.trim()||contact.email|| (contact.url?'企业联系页面':'企业联系入口');
}
export function contactChannels(contact:Contact){
 const kinds=new Set((contact.fields||[]).map(f=>f.key));
 return [contact.email||kinds.has('email')?'邮箱':'',kinds.has('phone')?'电话':'',kinds.has('whatsapp')?'WhatsApp':'',contact.url||kinds.has('url')?'官网联系页面':''].filter(Boolean).join(' / ')||'来源资料';
}
export function contactRelation(contact:Contact){
 // U-20: the backend tier (e.g. 具名·岗位未明确 for christian.arredondo@) replaces the old guess.
 if(contact.tier_label)return contact.tier_label;
 if(contact.kind==='procurement'&&['explicit','department'].includes(contact.role_status))return contact.name?.trim()&&contact.role_status==='explicit'?'采购人员':'采购部门 / 渠道';
 return contact.association_status==='company_only'||contact.kind==='contact_form'?'企业通用入口':'岗位未明确';
}
