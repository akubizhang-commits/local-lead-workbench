export interface WorkerHeartbeat {alive:boolean;last_seen:string|null}
export function workerStatus(worker?:WorkerHeartbeat){
 if(!worker)return {tone:'unknown',text:'后台采集状态未知'};
 return worker.alive?{tone:'alive',text:'后台采集在线'}:{tone:'offline',text:'后台采集离线'};
}
export function queueReason(status:string,worker?:WorkerHeartbeat){
 if(!['queued','running'].includes(status))return '';
 if(!worker)return '尚未取得后台心跳，无法确认是否正在采集。请刷新状态。';
 if(!worker.alive)return status==='running'?'后台心跳已中断，任务记录仍为执行中。请检查后台进程并刷新状态。':'等待后台采集进程：请启动本地后台进程后刷新状态；无需重复创建任务。';
 return status==='queued'?'排队等待后台处理，已有任务可能正在执行。':'';
}
