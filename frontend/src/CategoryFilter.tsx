import {useResource} from './hooks';
import type {CategoryCount} from './types';

/** Multi-select of Maps source categories (OR). Keyboard accessible: native checkboxes with labels. */
export function CategoryFilter({marketId,selected,version,onChange}:{marketId?:string;selected:string[];version:number;onChange:(categories:string[])=>void}) {
 const categories=useResource<CategoryCount[]>(`/company-categories?${new URLSearchParams({market_id:marketId||'',excluded:'hide'})}`,version);
 const chosen=new Set(selected.map(value=>value.trim().toLowerCase()));
 const toggle=(category:string,checked:boolean)=>onChange(checked?[...selected,category]:selected.filter(value=>value.trim().toLowerCase()!==category.trim().toLowerCase()));
 return <details className="category-filter">
  <summary>来源类别{selected.length?`（已选 ${selected.length}）`:''}</summary>
  <div className="category-options" role="group" aria-label="来源类别筛选">
   <button type="button" disabled={!selected.length} onClick={()=>onChange([])}>清除类别</button>
   {categories.error&&<p className="muted">类别列表暂不可用：{categories.error}</p>}
   {categories.data&&!categories.data.length&&<p className="muted">暂无来源类别</p>}
   {categories.data?.map(item=><label key={item.category_key}><input type="checkbox" checked={chosen.has(item.category.trim().toLowerCase())} onChange={e=>toggle(item.category,e.target.checked)}/>{item.category}（{item.count}）</label>)}
  </div>
 </details>;
}
