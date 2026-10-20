import './support/registerTsx.ts';
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

test('worker problem message replaces the heartbeat hint line',async()=>{
 const {createElement}=await import('react');
 const {renderToStaticMarkup}=await import('react-dom/server');
 const {WorkerHealth}=await import('../src/WorkerHealth.tsx');
 const html=renderToStaticMarkup(createElement(WorkerHealth,{worker:{alive:true,last_seen:'2026-10-18T00:00:00+00:00',problem:{code:'disk_full',message:'后台采集暂停：磁盘空间不足'}},onRefresh:()=>{}}));
 assert.match(html,/后台已暂停：磁盘空间不足/);
 const offline=renderToStaticMarkup(createElement(WorkerHealth,{worker:{alive:false,last_seen:null},onRefresh:()=>{}}));
 assert.match(offline,/API 连接正常不代表正在采集/);
 assert.doesNotMatch(offline,/磁盘/);
});
