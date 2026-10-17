import type {CompanyBrief} from './types';
import {External} from './ui';
export function CompanyLocation({company,title,onOpen}:{company:CompanyBrief|null;title?:string;onOpen:(id:string)=>void}) {
 return <article className="location-card">{title&&<small className="muted">{title}</small>}{company?<><button className="company-link" onClick={()=>onOpen(company.id)}><strong>{company.name}</strong></button><dl className="facts"><dt>地址</dt><dd>{company.address||'未记录'}</dd><dt>电话</dt><dd>{company.phone||'未记录'}</dd><dt>官网</dt><dd><External url={company.website}/></dd></dl><button className="text-button" onClick={()=>onOpen(company.id)}>查看这条企业记录 →</button></>:<p>这条企业记录已不可用，请保留待审核。</p>}</article>;
}
