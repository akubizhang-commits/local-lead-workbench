export interface WorkerProblem {code:string;message:string}
export interface WorkerHeartbeat {alive:boolean;last_seen:string|null;problem?:WorkerProblem}
export function workerStatus(worker?:WorkerHeartbeat){
 if(!worker)return {tone:'unknown',text:'后台状态未知'};
 return worker.alive?{tone:'alive',text:'后台在线'}:{tone:'offline',text:'后台离线'};
}
export function queueReason(status:string,worker?:WorkerHeartbeat){
 if(!['queued','running'].includes(status))return '';
 if(!worker)return '尚未取得后台心跳，无法确认是否正在采集。请刷新状态。';
 if(!worker.alive)return status==='running'?'后台心跳已中断，这一轮仍记为运行中。请检查后台是否在运行，然后刷新状态。':'等待后台：请启动本地后台后刷新状态，不需要重复开始。';
 return status==='queued'?'排队等待后台处理，后台可能正在处理其他轮次。':'';
}
