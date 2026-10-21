import {warningReasons} from './warningSummary';
import {useSession} from './session';

export function RunWarnings({error}: {error: string}) {
  const server=useSession().mode==='server';
  return <div className="run-warnings">
    {warningReasons(error,server).map(item=><p key={item.reason}><strong>{item.reason}</strong>：{item.action}</p>)}
    <details><summary>查看详细日志</summary><pre>{error}</pre></details>
  </div>;
}
