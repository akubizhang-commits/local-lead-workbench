import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

// P1-11 术语表（design §2.1 / §2.2）：前端源码（含注释）里的禁用叫法必须为 0，例外按“文件 + 字符串 + 次数”精确登记。
const SRC = new URL("../src/", import.meta.url).pathname;
function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}
// 先删掉两处必须保留后端原文的字面量：runMessage 的映射表，以及 warningSummary 里匹配后端原文的正则。
function source(path: string): string {
  let text = readFileSync(path, "utf8");
  const rel = relative(SRC, path);
  if (rel === "helpers.ts") {
    const start = text.indexOf("export const RUN_MESSAGE_MAP");
    const end = text.indexOf("export function runMessage");
    assert.ok(start >= 0 && end > start, "RUN_MESSAGE_MAP literal not found");
    text = text.slice(0, start) + text.slice(end);
  }
  if (rel === "warningSummary.ts") {
    const before = text;
    text = text.replace(/\/[^\n/]*未找到可执行的地图采集程序[^\n/]*\/i?/, "/REGEX/");
    assert.notEqual(text, before, "warningSummary regex literal not found");
  }
  return text;
}
const FORBIDDEN: (string | RegExp)[] = [
  // 目标市场
  "市场检索任务", "市场任务", "检索任务", "资料复核任务", "资料复核列表", "资料复核详情", "市场检索轮次",
  "发起任务", "创建任务",
  // 轮次
  "运行轮次", "检索轮次", "活动任务", "活动轮次", "执行中任务", "历史任务", "运行记录", "仅社媒轮次",
  // “任务”单独使用（只放行登记的“导出任务”）
  /任务/g,
  // 已处理 N / M
  "执行结束",
  // 企业（“本地线索工作台”是登记的例外）
  "企业线索", "企业记录", /线索/g,
  // 联系入口 / 联系渠道 / 联系人与账号
  "联系记录", "联系信息", "联系方式", "联系人 / 账号", "联系人/账号",
  // 官网复核 / 审核 / 定时计划
  "资料复核", "企业单独复核", "单独复核", "复核任务", "复核列表", "核验", "人工核验", "复核计划", "更新计划",
  // 地图检索（warningSummary 的正则字面量已在 source() 里删掉）
  "地图采集", "Maps 查询", "Maps 实际发现",
  // 后台 / 本地服务
  "后台采集", "后台 worker", "API 已连接", /(?<!本地)服务未连接/g, "API 连接正常",
  // 内部标识（各 1 次，只在诊断信息里）
  "企业编号", "运行编号",
  // 装饰性英文、第三方组件名
  "LEAD WORKBENCH", "LOCAL", "SearxNG", "Instaloader", "HTTP 读取",
];
// 精确例外：{file, text, count}；测试断言实际次数正好等于登记值，例外过期时会失败。
const EXCEPTIONS: { file: string; text: string; count: number }[] = [
  { file: "ExportJobs.tsx", text: "导出任务", count: 1 },
  { file: "App.tsx", text: "本地线索工作台", count: 2 },
  // AWS trial: product name on the login page and the server-mode window title (design §2.9).
  { file: "LoginPage.tsx", text: "寻迹线索工作台", count: 2 },
  { file: "AuthGate.tsx", text: "寻迹 · 线索工作台", count: 1 },
  { file: "SocialSettings.tsx", text: "SearxNG", count: 2 },
  { file: "SocialConnections.tsx", text: "Instaloader", count: 4 },
  { file: "SocialConnections.tsx", text: "YouTube API key", count: 2 },
  { file: "social/SocialPagePreview.tsx", text: "YouTube API", count: 1 },
  { file: "pages/CompanyDetailPage.tsx", text: "企业编号", count: 1 },
  { file: "pages/RunCard.tsx", text: "运行编号", count: 1 },
];
const count = (text: string, term: string | RegExp) =>
  typeof term === "string" ? text.split(term).length - 1 : [...text.matchAll(term)].length;

test("forbidden terms do not appear in frontend source except registered exceptions", () => {
  const hits: string[] = [];
  for (const path of files(SRC)) {
    const rel = relative(SRC, path);
    let text = source(path);
    for (const ex of EXCEPTIONS) if (ex.file === rel) text = text.split(ex.text).join("");
    for (const term of FORBIDDEN) {
      const n = count(text, term);
      if (n) hits.push(`${rel}: ${String(term)} × ${n}`);
    }
  }
  assert.deepEqual(hits, []);
});

test("each registered exception occurs exactly the registered number of times", () => {
  for (const ex of EXCEPTIONS) {
    const text = source(join(SRC, ex.file));
    assert.equal(count(text, ex.text), ex.count, `${ex.file}: ${ex.text}`);
  }
});

test("runMessage map keys and the warningSummary regex are the only place backend wording survives", () => {
  const helpers = readFileSync(join(SRC, "helpers.ts"), "utf8");
  assert.match(helpers, /\['等待本地 worker','等待后台处理'\]/);
  assert.match(readFileSync(join(SRC, "warningSummary.ts"), "utf8"), /未找到可执行的地图采集程序/);
});
