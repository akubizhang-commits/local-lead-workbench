import {useState} from 'react';
import {CompanyLocation} from './CompanyLocation';
import type {CompanyBrief} from './types';
export function RelatedLocations({companies,onOpen}:{companies:CompanyBrief[];onOpen:(id:string)=>void}){
 const [search,setSearch]=useState('');
 const shown=companies.filter(c=>[c.name,c.address,c.phone,c.city,c.country].join(' ').toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
 if(!companies.length)return null;
 return <details className="related-locations"><summary>同官网的其他门店（{companies.length}）</summary><p className="muted">可能属于同一品牌，仅关联展示。各地点资料分别保留。</p><label className="related-search">搜索关联门店<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="名称、地址、电话或地区"/></label><p className="muted">匹配 {shown.length} / {companies.length} 家</p><div className="location-comparison">{shown.map(c=><CompanyLocation key={c.id} company={c} onOpen={onOpen}/>)}</div>{!shown.length&&<p>没有匹配的关联门店。</p>}</details>;
}
