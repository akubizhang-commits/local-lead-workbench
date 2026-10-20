import {useState} from 'react';
import {useResource} from './hooks';
import type {CategoryCount} from './types';

const INLINE_LIMIT=8;
/**
 * Multi-select of Maps source categories (OR). Keyboard accessible: native checkboxes with labels.
 * `inline` (narrow filter drawer): a static fieldset instead of the floating dropdown, first 8 shown.
 */
export function CategoryFilter({marketId,selected,version,onChange,inline=false}:{marketId?:string;selected:string[];version:number;onChange:(categories:string[])=>void;inline?:boolean}) {
 const categories=useResource<CategoryCount[]>(`/company-categories?${new URLSearchParams({market_id:marketId||'',excluded:'hide'})}`,version);
 const [all,setAll]=useState(false);
 const chosen=new Set(selected.map(value=>value.trim().toLowerCase()));
 const toggle=(category:string,checked:boolean)=>onChange(checked?[...selected,category]:selected.filter(value=>value.trim().toLowerCase()!==category.trim().toLowerCase()));
 const list=categories.data||[];
 const shown=inline&&!all?list.slice(0,INLINE_LIMIT):list;
 const options=<>
  <button type="button" disabled={!selected.length} onClick={()=>onChange([])}>清除类别</button>
  {categories.error&&<p className="muted">类别列表暂不可用：{categories.error}</p>}
  {categories.data&&!categories.data.length&&<p className="muted">暂无来源类别</p>}
  {shown.map(item=><label key={item.category_key}><input type="checkbox" checked={chosen.has(item.category.trim().toLowerCase())} onChange={e=>toggle(item.category,e.target.checked)}/>{item.category}（{item.count}）</label>)}
  {inline&&!all&&list.length>INLINE_LIMIT&&<button type="button" className="text-button" onClick={()=>setAll(true)}>显示全部 {list.length} 个类别</button>}
 </>;
 if(inline)return <fieldset className="category-inline"><legend>来源类别{selected.length?`（已选 ${selected.length}）`:''}</legend><div className="category-inline-options" role="group" aria-label="来源类别筛选">{options}</div></fieldset>;
 return <details className="category-filter">
  <summary>来源类别{selected.length?`（已选 ${selected.length}）`:''}</summary>
  <div className="category-options" role="group" aria-label="来源类别筛选">{options}</div>
 </details>;
}
