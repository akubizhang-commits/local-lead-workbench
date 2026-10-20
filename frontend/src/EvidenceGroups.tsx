import {useState} from 'react';
import {date, display, label} from './helpers';
import type {CompanyDetail} from './types';
import {Badge, External} from './ui';
type Observation=CompanyDetail['observations'][number];
const SHOW=3;
/** Group observations by field, newest first. */
export function groupEvidence(items:Observation[]):[string,Observation[]][] {
 const groups=new Map<string,Observation[]>();
 for(const o of items){const list=groups.get(o.field)||[];list.push(o);groups.set(o.field,list);}
 return Array.from(groups.entries()).map(([field,list])=>[field,[...list].sort((a,b)=>String(b.observed_at).localeCompare(String(a.observed_at)))]);
}
/** “业务类型 3 · 联系电话 2 …”，最多 6 个字段。 */
export function evidenceSummary(items:Observation[]):string {
 const groups=groupEvidence(items);
 const parts=groups.slice(0,6).map(([field,list])=>`${label(field)} ${list.length}`);
 return parts.join(' · ')+(groups.length>6?` 等 ${groups.length} 个字段`:'');
}
function Group({field,items}:{field:string;items:Observation[]}) {
 const [all,setAll]=useState(false);
 const shown=all?items:items.slice(0,SHOW);
 return <section className="evidence-group">
  <h4>{label(field)} · {items.length} 条</h4>
  <div className="evidence-list">{shown.map(o=><article key={o.id}>
   <div className="row between"><strong>{label(o.field)} · {display(o.value)}</strong><Badge value={o.method}/></div>
   <blockquote>{o.evidence||'未提供证据片段'}</blockquote>
   <div className="row between"><External url={o.source_url}/><time>{date(o.observed_at)}</time></div>
  </article>)}</div>
  {!all&&items.length>SHOW&&<button type="button" className="text-button" onClick={()=>setAll(true)}>显示其余 {items.length-SHOW} 条</button>}
 </section>;
}
export function EvidenceGroups({items}:{items:Observation[]}) {
 return <>{groupEvidence(items).map(([field,list])=><Group key={field} field={field} items={list}/>)}</>;
}
