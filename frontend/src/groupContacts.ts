export function adoptionStatus(contact:{adoption_status?:string;review_status?:string}){
 return contact.adoption_status || (['reject','rejected'].includes(contact.review_status||'')?'excluded':'automatic');
}
export function isHiddenContact(contact:{review_status?:string}) {
 return ['invalid','superseded'].includes(contact.review_status || '');
}
export function groupContacts<T extends {name?:string;kind:string;role_status:string;adoption_status?:string;review_status?:string;outreach_category?:string}>(contacts:T[]) {
 const procurement:T[]=[],channels:T[]=[],other:T[]=[],excluded:T[]=[],pending:T[]=[],irrelevant:T[]=[];
 for(const contact of contacts){
  if(isHiddenContact(contact))continue;
  if(adoptionStatus(contact)==='excluded'){excluded.push(contact);continue;}
  if(contact.review_status==='pending'){pending.push(contact);continue;}
  if(contact.outreach_category==='irrelevant'){irrelevant.push(contact);continue;}
  const hasProcurementRole=contact.kind==='procurement'&&['explicit','department'].includes(contact.role_status);
  if(!hasProcurementRole){other.push(contact);continue;}
  (contact.role_status==='explicit' && contact.name?.trim()?procurement:channels).push(contact);
 }
 return {procurement,channels,other,excluded,pending,irrelevant};
}

export function usableContactCount(contacts:Parameters<typeof groupContacts>[0]){
 const groups=groupContacts(contacts);return groups.procurement.length+groups.channels.length+groups.other.length;
}
