import {label} from './helpers';
import type {Company} from './types';
export const companyStatuses=['candidate','verified','not_fit','do_not_contact'];
export function StatusOptions(){return <>{companyStatuses.map(status=><option key={status} value={status}>{label(status)}</option>)}</>;}
export function CompanyStatus({company,busy,onChange}:{company:Company;busy:boolean;onChange:(company:Company,status:string)=>void}){
 return <label className="quick-status"><span className="sr-only">{company.name} 客户匹配状态</span><select aria-label={`${company.name} 客户匹配状态`} value={company.status} disabled={busy} onChange={e=>onChange(company,e.target.value)}><StatusOptions/></select></label>;
}
