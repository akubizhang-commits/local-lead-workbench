import type {Company, Contact} from './types';
import {tierClass, tierLabel} from './helpers';
const usable=(c:Contact)=>c.adoption_status!=='excluded'&&!['pending','reject','invalid','superseded'].includes(c.review_status)&&c.outreach_category!=='irrelevant';
/** Best entry: who (name / role) and, for e-mail or account entries, the value on its own line. */
export function bestEntry(company:Company,contacts:Contact[]):{who:string;value:string} {
 if(company.best_tier==='company_phone')return {who:company.phone||'',value:''};
 if(!company.best_tier||company.best_tier==='none')return {who:'',value:''};
 const best=contacts.find(c=>usable(c)&&c.tier===company.best_tier);
 if(!best)return {who:'',value:''};
 const name=best.name?.trim();
 const who=name?`${name}${best.role?.trim()?`（${best.role.trim()}）`:''}`:'';
 const value=best.email||best.url||best.phone||'';
 return who?{who,value}:{who:value,value:''};
}
/** "通用邮箱 · info@co.example" (kept for callers that need a single line). */
export function bestEntryText(company:Company,contacts:Contact[]) {
 const label=company.best_tier_label||tierLabel(company.best_tier);
 const {who}=bestEntry(company,contacts);
 return who?`${label} · ${who}`:label;
}
/** 企业详情概要左列（design §4.2 第 3 条）。 */
export function ContactEntrySummary({company,contacts,onJump}:{company:Company;contacts:Contact[];onJump?:()=>void}) {
 const {who,value}=bestEntry(company,contacts);
 return <div className="contact-entry-summary" aria-labelledby="entry-summary-title">
  <h3 id="entry-summary-title" tabIndex={-1} data-anchor-heading="">联系入口</h3>
  <p><span className={tierClass(company.best_tier_rank)}>{company.best_tier_label||tierLabel(company.best_tier)}</span>{who&&<> {who}</>}</p>
  {value&&<p className="entry-value">{value}</p>}
  <p className="muted">联系入口 {company.contact_count} 个（已去重） · 缺口：{company.gap?.reason||'无'}</p>
  {company.gap&&<p>下一步：{company.gap.next_step}</p>}
  {onJump&&<button type="button" className="text-button" onClick={onJump}>查看全部联系入口 ↓</button>}
 </div>;
}
