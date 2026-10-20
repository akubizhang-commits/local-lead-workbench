import {date,runMessage} from './helpers';
import {workerStatus,type WorkerHeartbeat} from './workerStatus';
export function WorkerHealth({worker,onRefresh}:{worker?:WorkerHeartbeat;onRefresh:()=>void}){
 const status=workerStatus(worker);
 return <section className={`worker-health worker-${status.tone}`} aria-label="后台状态" role="status"><div><strong>{status.text}</strong><span>最近心跳：{date(worker?.last_seen)}</span>{worker?.problem?.message?<p>{runMessage(worker.problem.message)}</p>:status.tone!=='alive'&&<p>本地服务连接正常，不代表后台在运行。请检查后台是否已启动，然后刷新状态；排队中的轮次会保留。</p>}</div><button onClick={onRefresh}>刷新后台状态</button></section>;
}
