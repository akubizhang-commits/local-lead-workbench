export interface WebsiteSummary {
 total:number;processed:number;with_pages:number;without_pages:number;pending:number;running:number;
 files_unparsed_companies:number;
 reasons:{code:string;label:string;companies:number;with_pages:number;without_pages:number}[];
}

function pageResults(summary:WebsiteSummary) {
 return `已读取网页 ${summary.with_pages} 家 · 未读到网页 ${summary.without_pages} 家`;
}
function reasonText(reason:WebsiteSummary['reasons'][number]) {
 return `${reason.label} ${reason.companies} 家${reason.with_pages>0?`（其中 ${reason.with_pages} 家有部分网页结果）`:''}`;
}
export function websiteDetailsTip(summary:WebsiteSummary|undefined,processed:number) {
 return summary?[`本轮已处理 ${summary.processed} 家企业`,pageResults(summary),...summary.reasons.map(reasonText)].join('；'):
  `本轮已处理 ${processed} 家企业；网页读取企业数暂未统计。`;
}

export function WorkflowWebsiteDetails({summary,processed,pending,running,ended}:{
 summary?:WebsiteSummary;processed:number;pending:number;running:number;ended:boolean;
}) {
 const waiting=summary?.pending??pending,working=summary?.running??running;
 return <>
  <p>本轮已处理 {summary?.processed??processed} 家企业{summary&&`；本轮官网补全共 ${summary.total} 家`}。</p>
  {summary?<p><strong>{pageResults(summary)}</strong></p>:<p>网页读取企业数暂未统计。</p>}
  {(waiting>0||working>0)&&<div className="workflow-breakdown">
   {waiting>0&&<span>待处理 {waiting} 家</span>}
   {working>0&&<span>处理中 {working} 家</span>}
  </div>}
  {!!summary?.reasons.length&&<div>
   <p>原因按本轮企业计数，每家只计一个主要原因；网页结果仅统计已结束企业。</p>
   {summary.reasons.map(reason=><p key={reason.code}>{reasonText(reason)}</p>)}
  </div>}
  {!!summary?.files_unparsed_companies&&<p>另有 {summary.files_unparsed_companies} 家企业包含未解析文件；未解析文件不计为访问失败。</p>}
  {ended&&<p>本次执行已结束，未能读取的来源不影响已保存资料；重试不保证网站恢复。</p>}
 </>;
}
