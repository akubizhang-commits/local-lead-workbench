import {useState} from 'react';
import {api, request} from './api';
import {useResource} from './hooks';
import type {Action} from './pages/pageTypes';
import type {CategoryCount, ExclusionRule} from './types';

export function ruleText(rule:ExclusionRule,marketId?:string) {
 const scope=rule.scope==='global'?'全局':rule.market_id===marketId?'本市场':'其他市场';
 return `${rule.kind==='category'?'类别':'关键词'}：${rule.value} · ${scope} · 命中 ${rule.matched} 家`;
}

/** Exclusion rules hide matching companies by default; nothing is deleted and no status changes. */
export function ExclusionRules({marketId,version,busy,act}:{marketId?:string;version:number;busy:boolean;act:Action}) {
 const scopeQuery=new URLSearchParams({market_id:marketId||''});
 const rules=useResource<ExclusionRule[]>(`/exclusion-rules?${scopeQuery}`,version);
 const categories=useResource<CategoryCount[]>(`/company-categories?${new URLSearchParams({market_id:marketId||'',excluded:'include'})}`,version);
 const [category,setCategory]=useState('');
 const [keyword,setKeyword]=useState('');
 const target=marketId?{scope:'market',market_id:marketId}:{scope:'global'};
 const add=(kind:'category'|'keyword',value:string,reset:()=>void)=>act(async()=>{const rule=await api<ExclusionRule>('/exclusion-rules','POST',{...target,kind,value});reset();return rule;},
  result=>(result as ExclusionRule).reused?'已有相同的排除规则':'已添加排除规则，命中的企业默认隐藏');
 return <section className="panel exclusion-rules" aria-labelledby="exclusion-rules-title">
  <h3 id="exclusion-rules-title">排除规则</h3>
  <p className="muted">命中规则的企业默认隐藏，不会删除，也不改变客户匹配状态；删除规则即可恢复显示。</p>
  <form className="row" onSubmit={e=>{e.preventDefault();if(category)add('category',category,()=>setCategory(''));}}>
   <label>排除类别 <select value={category} onChange={e=>setCategory(e.target.value)} disabled={busy}>
    <option value="">选择来源类别</option>
    {categories.data?.map(item=><option key={item.category_key} value={item.category}>{item.category}（{item.count}）</option>)}
   </select></label>
   <button type="submit" disabled={busy||!category}>排除此类别</button>
  </form>
  <form className="row" onSubmit={e=>{e.preventDefault();if(keyword.trim())add('keyword',keyword.trim(),()=>setKeyword(''));}}>
   <label>排除关键词 <input value={keyword} maxLength={120} placeholder="如 contractor、roofing contractor" onChange={e=>setKeyword(e.target.value)} disabled={busy}/></label>
   <button type="submit" disabled={busy||keyword.trim().length<2}>添加排除关键词</button>
  </form>
  <p className="hint">关键词按包含匹配，例如 roof 也会命中 Roofing supply。</p>
  {rules.error&&<p className="muted">规则列表暂不可用：{rules.error}</p>}
  {rules.data&&!rules.data.length&&<p className="muted">还没有排除规则。</p>}
  <ul className="rule-list">{rules.data?.map(rule=><li key={rule.id}><span>{ruleText(rule,marketId)}</span>
   <button type="button" disabled={busy} aria-label={`删除规则 ${rule.value}`} onClick={()=>act(()=>request(`/exclusion-rules/${encodeURIComponent(rule.id)}`,{method:'DELETE'}),'已删除排除规则，相关企业恢复显示')}>删除</button></li>)}</ul>
 </section>;
}
