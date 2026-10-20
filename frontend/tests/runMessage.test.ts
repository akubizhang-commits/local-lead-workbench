import test from "node:test";
import assert from "node:assert/strict";
import { RUN_MESSAGE_MAP, mapsReasonText, runMessage } from "../src/helpers.ts";

// Backend wording → user-facing wording (design §2.2, review round 3 R1). One case per table row.
const CASES: [string, string][] = [
  ["等待本地 worker", "等待后台处理"],
  ["恢复中断任务", "后台重启后继续处理"],
  ["部分任务未完成，请查看原因", "本轮部分未完成，请查看各阶段原因"],
  ["worker 停止，等待恢复", "后台已停止，重新启动后继续"],
  ["后台采集等待数据库初始化…", "后台正在等待数据库初始化…"],
  ["后台采集暂停：数据库无法打开", "后台已暂停：数据库无法打开"],
  ["旧任务已暂停，本期更新将延后；继续或取消旧任务后再调度",
    "上一轮已暂停，本期自动更新会延后；到市场详情继续上一轮，等它结束后会按计划更新"],
  ["当前策略覆盖能力已耗尽：本轮检索任务预算已用尽；已保留已取得的结果",
    "当前策略覆盖能力已耗尽：本轮检索预算已用尽；已保留已取得的结果"],
  ["任务已暂停，等待手动继续", "本轮已暂停，等待手动继续"],
  ["等待后台调度，本任务与其他任务轮流执行", "等待后台处理，本轮和其他轮次轮流进行"],
  ["社媒任务等待登录或连接处理", "社媒补充等待登录或连接处理"],
  ["社媒任务等待调度或平台访问间隔", "社媒补充等待后台处理或平台访问间隔"],
  ["本轮仍有待处理任务，已取得的结果已保存", "本轮仍有待处理项，已取得的结果已保存"],
  ["地图采集保护暂停：连续失败。请确认访问已恢复后解除暂停", "地图检索保护暂停：连续失败。请确认访问已恢复后解除暂停"],
  ["地图采集低速等待：约2分钟后继续，已有结果已保存", "地图检索低速等待：约2分钟后继续，已有结果已保存"],
  ["地图采集低速模式：单并发，查询结束后间隔1～2分钟", "地图检索低速模式：单并发，查询结束后间隔1～2分钟"],
  ["地图连接或采集进程异常", "地图连接或检索程序异常"],
  ["地图采集程序不可用，需要检查运行环境", "地图检索程序不可用，需要检查运行环境"],
  ["上次的地图采集进程无法自动结束，地图检索已暂停。", "上次的地图检索程序无法自动结束，地图检索已暂停。"],
];

test("RUN_MESSAGE_MAP has 19 entries, longest source first", () => {
  assert.equal(RUN_MESSAGE_MAP.length, 19);
  for (let i = 1; i < RUN_MESSAGE_MAP.length; i++)
    assert.ok(RUN_MESSAGE_MAP[i - 1][0].length >= RUN_MESSAGE_MAP[i][0].length);
});

for (const [from, to] of CASES) {
  test(`runMessage maps “${from.slice(0, 16)}”`, () => assert.equal(runMessage(from), to));
}

test("a mapped prefix keeps the appended suffix", () => {
  assert.equal(runMessage("部分任务未完成，请查看原因；社媒已处理 3/5 家企业"),
    "本轮部分未完成，请查看各阶段原因；社媒已处理 3/5 家企业");
});

test("unknown text is unchanged and nullish becomes empty", () => {
  assert.equal(runMessage("本轮处理结束，已取得的企业和资料已保存"), "本轮处理结束，已取得的企业和资料已保存");
  assert.equal(runMessage(null), "");
  assert.equal(runMessage(undefined), "");
});

test("no mapped output contains internal wording", () => {
  for (const [from] of CASES) assert.doesNotMatch(runMessage(from), /worker|任务|地图采集/);
  for (const [, to] of RUN_MESSAGE_MAP) assert.doesNotMatch(to, /worker|任务|地图采集/);
});

test("mapsReasonText maps Chinese backend text through runMessage", () => {
  assert.equal(mapsReasonText("地图连接或采集进程异常", "timeout"), "地图连接或检索程序异常");
});
