import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const { backupStatusLines, BackupStatusLines, backupLabel, RETENTION_UNCONFIRMED } = await import("../src/pages/SettingsPage.tsx");
const { date } = await import("../src/helpers.ts");

const base = {
  daily_enabled: true, time: "03:30", timezone: "Asia/Shanghai", last_daily_at: "2026-10-20T19:30:05+00:00",
  last_daily_status: "ok" as const, last_daily_error: null, next_due_at: null, retention_active: true,
  s3: { configured: true, last_status: "ok" as const, last_at: "2026-10-20T19:31:00+00:00", error_code: null },
};

test("enabled and uploaded", () => {
  assert.deepEqual(backupStatusLines(base), [
    `每日 03:30 自动备份 · 上次：${date(base.last_daily_at)}`, `已上传到 S3（${date(base.s3.last_at)}）`]);
});

test("S3 not configured, failed, or waiting", () => {
  assert.equal(backupStatusLines({ ...base, s3: { ...base.s3, configured: false } })[1], "未配置 S3 上传");
  assert.equal(backupStatusLines({ ...base, s3: { ...base.s3, last_status: "failed", error_code: "AccessDenied" } })[1],
    "S3 上传失败（AccessDenied），已保留本地备份；请检查实例角色和存储桶设置");
  assert.equal(backupStatusLines({ ...base, s3: { ...base.s3, last_status: null, last_at: null } })[1],
    "S3 上传：等待下一份每日备份");
});

test("disabled and failed local backup", () => {
  assert.deepEqual(backupStatusLines({ ...base, daily_enabled: false }), ["每日自动备份未开启"]);
  assert.equal(backupStatusLines({ ...base, last_daily_status: "failed", last_daily_error: "disk_full" })[0],
    "上次每日备份失败（disk_full），1 小时后重试");
});

test("unconfirmed retention shows the alert only when daily backups run", () => {
  const html = renderToStaticMarkup(createElement(BackupStatusLines, { status: { ...base, retention_active: false } }));
  assert.ok(html.includes(RETENTION_UNCONFIRMED) && html.includes('role="alert"'));
  assert.equal(RETENTION_UNCONFIRMED, "存储保留策略尚未确认，自动备份不会被清理，请在下方确认");
  const off = renderToStaticMarkup(createElement(BackupStatusLines, { status: { ...base, daily_enabled: false, retention_active: false } }));
  assert.ok(!off.includes(RETENTION_UNCONFIRMED));
});

test("daily backups have their own label", () => {
  assert.equal(backupLabel({ name: "auto-daily-x.sqlite3", created_at: "", kind: "auto", reason: "daily" }), "自动备份（每日）");
  assert.equal(backupLabel({ name: "auto-pre-repair-x.sqlite3", created_at: "", kind: "auto", reason: "pre-repair" }), "自动备份（修复前）");
});
