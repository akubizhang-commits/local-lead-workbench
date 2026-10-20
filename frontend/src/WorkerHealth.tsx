import {date,runMessage} from './helpers';
import {workerStatus,type WorkerHeartbeat} from './workerStatus';
export function WorkerHealth({worker,onRefresh}:{worker?:WorkerHeartbeat;onRefresh:()=>void}){
 const status=workerStatus(worker);
 return <section className={`worker-health worker-${status.tone}`} aria-label="后台采集状态" role="status"><div><strong>{status.text}</strong><span>最近心跳：{date(worker?.last_seen)}</span>{worker?.problem?.message?<p>{runMessage(worker.problem.message)}</p>:status.tone!=='alive'&&<p>API 连接正常不代表正在采集。请检查本地后台进程是否启动，再刷新状态；排队任务会保留。</p>}</div><button onClick={onRefresh}>刷新后台状态</button></section>;
}
