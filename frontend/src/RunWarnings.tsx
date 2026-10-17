import {warningReasons} from './warningSummary';

export function RunWarnings({error}: {error: string}) {
  return <div className="run-warnings">
    {warningReasons(error).map(item=><p key={item.reason}><strong>{item.reason}</strong>：{item.action}</p>)}
    <details><summary>查看详细日志</summary><pre>{error}</pre></details>
  </div>;
}
