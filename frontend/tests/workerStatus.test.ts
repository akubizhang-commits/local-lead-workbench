import test from 'node:test';
import assert from 'node:assert/strict';
import {workerStatus,queueReason} from '../src/workerStatus.ts';
test('API connectivity never implies worker liveness',()=>{
 assert.equal(workerStatus(undefined).tone,'unknown');
 assert.equal(workerStatus({alive:false,last_seen:null}).tone,'offline');
 assert.match(queueReason('queued',{alive:false,last_seen:null}),/启动.*后台/);
 assert.match(queueReason('queued',{alive:true,last_seen:'2026-09-22'}),/排队/);
 assert.match(queueReason('running',{alive:false,last_seen:'2026-09-22'}),/中断|心跳/);
 assert.equal(queueReason('completed',{alive:false,last_seen:null}),'');
});
