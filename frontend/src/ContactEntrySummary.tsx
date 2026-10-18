import type {Company, Contact} from './types';
import {tierClass, tierLabel} from './helpers';

const usable=(c:Contact)=>c.adoption_status!=='excluded'&&!['pending','reject','invalid','superseded'].includes(c.review_status)&&c.outreach_category!=='irrelevant';

/** "最佳入口：具名采购 · Bob Buyer（Buyer）" or the company phone / none. */
export function bestEntryText(company:Company,contacts:Contact[]) {
 const label=company.best_tier_label||tierLabel(company.best_tier);
 if(company.best_tier==='company_phone')return `${label} · ${company.phone}`;
 if(!company.best_tier||company.best_tier==='none')return label;
 const best=contacts.find(c=>usable(c)&&c.tier===company.best_tier);
 if(!best)return label;
 const who=best.name?.trim()?`${best.name.trim()}${best.role?.trim()?`（${best.role.trim()}）`:''}`:best.email||best.url||best.phone||'';
 return who?`${label} · ${who}`:label;
}

export function ContactEntrySummary({company,contacts,onAdd,adding}:{company:Company;contacts:Contact[];onAdd:()=>void;adding:boolean}) {
 return <section className="panel contact-entry-summary" aria-labelledby="entry-summary-title">
  <div className="section-title"><h3 id="entry-summary-title">联系入口</h3><button onClick={onAdd} aria-expanded={adding}>新增联系人</button></div>
  <p><span className={tierClass(company.best_tier_rank)}>{company.best_tier_label||tierLabel(company.best_tier)}</span> 最佳入口：{bestEntryText(company,contacts)}</p>
  <p className="muted">联系入口 {company.contact_count} 个（已去重）</p>
  {company.gap&&<><p>缺口：{company.gap.reason}</p><p>下一步：{company.gap.next_step}</p></>}
 </section>;
}
