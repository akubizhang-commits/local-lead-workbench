import {label, tierClass} from './helpers';
import type {Company} from './types';
import {Icon} from './ui';

/**
 * Narrow (<780px) company list: one button per company (design §4.4). The accessible name is the
 * button text itself (no aria-label), so screen readers hear city, entry tier, match status and gap.
 * Only <span>s inside the button; the full name goes in `title` because the visual line is clamped.
 */
export function CompanyCards({items,marketId,onOpen}:{items:Company[];marketId?:string;onOpen:(id:string)=>void}) {
 return <ul className="company-cards" id="company-list" tabIndex={-1} aria-label="企业列表">
  {items.map(c=>{
   const parsed=c.actual_city_basis==='address'&&!!c.actual_city;
   const place=[parsed?c.actual_city:c.city,c.country].filter(Boolean).join(' / ')||'地区未记录';
   return <li key={c.id}>
    <button type="button" className="company-card" title={c.name} onClick={()=>onOpen(c.id)}>
     <span className="card-line card-name"><span className="sr-only">查看 </span>{c.name}</span>
     <span className="card-line card-meta">{place}{marketId&&c.market_segment?` · ${label(c.market_segment)}`:''}{parsed&&c.nearby?' · 邻近城市':''}</span>
     <span className="card-line"><span className={tierClass(c.best_tier_rank)}>{c.best_tier_label||'—'}</span> 联系入口 {c.contact_count} 个</span>
     <span className="card-line"><span className={`badge badge-${c.status}`}>{label(c.status)}</span>{c.gap&&<span className="card-gap"> 缺口：{c.gap.reason}</span>}</span>
     {c.exclusion&&<span className="card-line"><span className="badge badge-excluded">已排除：{c.exclusion.kind==='category'?'类别':'关键词'} {c.exclusion.value}</span></span>}
     <span className="card-chevron" aria-hidden="true"><Icon name="arrow" size={16}/></span>
    </button>
   </li>;
  })}
 </ul>;
}
