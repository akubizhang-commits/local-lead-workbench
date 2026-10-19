import type {Incompleteness, Task} from './types';
import {TIERS, date} from './helpers';
import {relocateRun} from './api';
import type {Action} from './pages/pageTypes';
/** Confirmation text; 1 root + 16 cells per search term once the city bounds are found. */
export const relocateConfirm=(terms:number)=>`将重新定位城市范围，成功后按范围补查，预计新增约 ${17*Math.max(1,terms)} 次地图检索（按访问保护逐个执行，可随时暂停）。继续吗？`;
export function RelocateNotice({coverage,runId,busy,act}:{coverage?:Incompleteness;runId?:string|null;busy?:boolean;act?:Action}) {
 if(!coverage?.incomplete)return null;
 const disabled=!runId||!act||busy||!coverage.relocatable;
 return <div className="alert relocate-notice" role="status">
  <strong>结果不完整：城市范围定位失败，本轮只取得首批结果。</strong>
  {coverage.geocoder_retry_at&&<span> 定位服务冷却中，请在 {date(coverage.geocoder_retry_at)} 后重试。</span>}
  <div className="row"><button disabled={disabled} onClick={()=>{if(runId&&act&&window.confirm(relocateConfirm(coverage.query_terms?.length||1)))act(()=>relocateRun(runId),'已加入补查队列：将重新定位城市范围并按范围补查');}}>重试定位并补查</button>
  {!coverage.relocatable&&coverage.relocate_block_reason&&<small className="muted">{coverage.relocate_block_reason}</small>}</div>
 </div>;
}
export function MarketSummary({market,onTier,onNearby,busy,act}:{market:Task;onTier?:(tier:string)=>void;onNearby?:()=>void;busy?:boolean;act?:Action}) {
 return <><RelocateNotice coverage={market.coverage} runId={market.coverage?.run_id} busy={busy} act={act}/><div className="market-stats">{([['total','市场企业'],['new','本轮新增'],['existing','存量企业'],['changed','资料变化'],['not_seen','未再次找到']] as const).map(([key,title])=><div key={key}><strong>{market.market_stats?.[key]??'—'}</strong><span>{title}</span></div>)}{market.market_stats?.nearby!==undefined&&<div className="market-nearby">{onNearby?<button className="text-button" onClick={onNearby}>邻近城市 {market.market_stats.nearby} 家</button>:<span>邻近城市 {market.market_stats.nearby} 家</span>}</div>}</div><div className="market-coverage"><span>{market.coverage?.scope_label||'等待开始检索'}{market.coverage?.bbox?' · 地区范围参考，非精确行政边界':''}</span><span>查询 {market.coverage?.done??'—'} / {market.coverage?.planned??'—'} · 未解决 {market.coverage?.unresolved.length??'—'}</span>{market.coverage?.query_basis&&<span className="market-query-terms">{market.coverage.query_basis==='keywords'?'检索词（按关键词）':'检索词（旧规则：按企业类型使用服装行业默认词）'}：{(market.coverage.query_terms||[]).join('、')}</span>}{!!market.coverage?.unresolved.length&&<details className="coverage-reasons"><summary>查看未解决原因</summary><ul>{market.coverage.unresolved.map((reason,index)=><li key={index}>{reason}</li>)}</ul></details>}</div>{market.tier_stats&&<div className="market-tiers"><span>联系入口等级（按每家最佳入口计）：</span>{TIERS.map(([code,title],index)=><span key={code}>{index>0&&' · '}<button className="text-button" onClick={()=>onTier?.(code)}>{title} {market.tier_stats?.[code as keyof typeof market.tier_stats]??0}{index===0?' 家':''}</button></span>)}</div>}{!!market.market_stats?.excluded&&<span className="market-excluded">已按排除规则隐藏 {market.market_stats.excluded} 家</span>}{market.coverage?.attribution&&<span className="market-attribution">范围数据来源：{market.coverage.attribution}</span>}</>;
}
