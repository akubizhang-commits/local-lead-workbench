# 市场社媒集成：实施审计

日期：2026-09-25  
状态：**实现及本地部署验收完成，评审问题全部关闭；后端 705 项及另 44 个子测试通过，前端 111 项及构建通过。API、Worker 已重启，原有数据与三平台 Cookies 文件保留，未触发历史社媒补采。**

## 范围与依据

已批准计划：[市场任务内的社媒检索](../superpowers/plans/2026-09-25-market-social-integration.md)。规范入口：[PRD](../product/overseas-lead-acquisition.prd.md)、[交互设计](../product/interaction-design.md)、[集成技术设计](../technical/market-social-integration.md)、[进度协议](../technical/task-workflow-progress.md)。

本轮以稳定为先：地图、官网、社媒三个通道各最多 1 项；平台正常间隔 60–120 秒；网络/限流采用 10、30、120 分钟冷却，三次自动恢复后转人工。登录隔离、暂停/取消后的迟到结果、历史不自动重放、独立手动任务不被市场控制误伤，均为放行条件。

## 实施核对

| 范围 | 当前工作树入口 | 核对结果 |
|---|---|---|
| 未来设置、轮次快照、显式补充与关联 | `market_social.py`、`api.py` | 新表单默认开启、三平台全选、帖子关闭；后端旧值默认关闭，不重放历史；活动轮次附加或新建仅社媒轮次 |
| 平台节奏、门控与恢复 | `social_policy.py`、`social_discovery_store.py` | 持久化独立门控，登录只暂停受影响平台；恢复不能跳过冷却，重载连接配置但保留业务快照 |
| 三个执行通道与退出清理 | `worker.py`、`worker_scheduler.py`、`work_item_executor.py` | 已改为 `ParallelWorkScheduler`，生命周期持有恢复锁 |
| 市场进度与社媒报告 | `task_workflow.py`、`TaskWorkflow.tsx`、`WorkflowSocial.tsx` | 3A/3B 并行、动态企业计数、仅社媒轮次、平台操作及懒加载报告；上游未关闭或社媒仍 pending 时不冒充全部完成 |
| 候选与读取预算 | `market_social.py`、已有社媒 reader | 每企业每轮最多 8 个 profile 项；候选证据保留；近期帖子沿用默认 3/最多 5 配置 |
| 文档与附件 | 产品/技术规范、OpenAPI、DDL | 同步最终实现；本次评审修复未增加 API 或 DDL，仅刷新源码指纹与校验记录 |

## 评审关闭记录

| 问题 | 最终实现与回归依据 | 状态 |
|---|---|---|
| P1：后续滚动/帖子读取的登录、验证及访问错误丢失结构化原因 | `sources/social_browser.py` 保留 login_required/challenge/rate_limited/network_error 等 reason，已有预览及帖子保留为 partial；`social_policy.py` 依据 status/reason 保留对应等待。`tests/test_social_late_gate.py` 覆盖迟到登录/验证、HTTP、超时、初始受阻、正常成功及来源清理，**16 项通过** | 已修复并复核关闭 |
| P2：账号在读取中被排除后仍可能提交迟到结果 | `social_item_active` 与 `finish_social_item` 检查账号关联；Worker 协作取消并清理，提交事务丢弃迟到观察/帖子并结束该项，避免 running 孤项。`tests/test_market_social.py` 覆盖排除后的迟到提交与在途取消 | 已修复并复核关闭 |
| 企业内独立操作受市场子运行干扰 | SocialDiscovery 的活动判断排除市场子运行；手动创建运行的去重也排除市场归属。`frontend/tests/marketSocial.test.ts` 与 `tests/test_market_social.py` 覆盖独立按钮、父控制入口及归属隔离 | 已修复并复核关闭 |

## 其他实现核对

| 修复 | 当前行为 |
|---|---|
| 仅社媒轮次命名与 RunCard | `kind=social` 显示社媒轮次名称及社媒企业进度，省略虚构的地图/官网进度；`social_done/social_total` 按企业计数 |
| 子运行归属与控制入口 | SocialRun 返回可选 `parent_run_id/task_id`；企业 SocialDiscovery 展示“查看所属市场任务”，关联运行由父轮次统一暂停、继续或取消，手动独立运行保留自己的控制和独立去重范围 |
| 恢复连接配置 | 平台恢复重新读取已保存连接，并更新活动市场父轮次下未取消子运行的 connections；market_options、既有队列范围、其他平台门控与手动独立运行不变 |
| 领取令牌投影 | `claim_token` 仅供内部领取/结果裁决，不返回在 API 的 `items[].result` 中 |
| 市场最近发现统计 | 最新轮次为 social 时，市场 coverage 和 new/existing/changed 等发现统计仍取最近 discovery 轮次；社媒补充不覆盖历史发现成果 |
| 重试中的部分预览 | 已取得的有效基础资料/页面观察先保留，平台仍可回到 pending 等待重试；不丢弃部分成功内容，也不当作全部完成 |
| partial 汇总 | `result.partial=true` 纳入平台/社媒企业计数与父轮次异常汇总，即便原项 state 为 succeeded，也不显示全成功 |
| 旧备份恢复 | 恢复路径补执行 `_migrate_market_social`，确保三个新增表存在；缺历史设置仍默认关闭，不自动回放 |

Python 实现路径相对 `leadworkbench/`，前端实现路径相对 `frontend/src/`；测试路径相对项目根目录。

## 验证记录

| 检查 | 本轮状态 |
|---|---|
| 后端全量隔离测试 | **705 项通过，另 44 个子测试通过，耗时 35.43 秒**；子测试单列，不与主测试数相加。见[原始日志](market-social-evidence/backend-tests.log) |
| P1 结构化原因定向回归 | **16 项通过**；已包含在后端全量结果中，不重复累加 |
| 前端最终测试 | **111 项通过，0 失败**。见[原始日志](market-social-evidence/frontend-tests.log) |
| 前端最终构建 | **TypeScript（tsc）检查与 Vite 构建通过** |
| 验证证据归档 | 后端、前端原始测试日志与[部署核验 JSON](market-social-evidence/deployment.json)已归档至 `docs/validation/market-social-evidence/` |
| 隔离浏览器交互 | 已在 **18766** 端口验证：3A/3B 并行、登录门控操作及详情、**375px** 窄屏布局、新表单默认三平台且帖子关闭 |
| 只读评审 | **全部问题已修复并复核关闭** |
| 文档校验、OpenAPI 路由集合与内存 DDL | 2026-09-25 通过：52 个业务 API / 含 SPA 共 53 个操作、37 张表、8 个显式索引；integrity_check=ok，无外键错误。最终机器结果见 [validation.json](../technical/validation.json) |
| 本地服务与页面 | **API、Worker 已重启，API 端口 18765；Worker alive=true**。浏览器重新加载后已确认新增字段，设置区域已展开 |
| 停机备份与数据保留 | 最新停机备份 `data/backups/backup-before-market-social-20260925T144041Z.sqlite3`；重启核验为 **195 家企业、2831 个联系人、2 个市场任务、2 个轮次**，各类停机前 ID 全部保留，SQLite integrity=ok |
| 历史社媒不回放 | **social_runs=0、market_social_runs=0**；重启未创建历史社媒补采轮次，验收未额外启动真实采集或批量社媒 |
| 专用社媒会话文件 | Facebook、Instagram、X 的 **3 份 Cookies 文件均保留**；未验证线上认证是否仍有效，文件保留不等于登录可用 |

数据保留以停机时的最新备份为基准。原有任务在停机前继续执行，企业/联系人从早期快照的 187/2808 增至 195/2831；该增量不是数据丢失，也不是本次部署触发的历史社媒回放。

回归入口：`tests/test_market_social.py`、`tests/test_market_social_api.py`、`tests/test_social_late_gate.py`、`tests/test_parallel_worker.py`、`frontend/tests/marketSocial.test.ts`、`frontend/tests/geographyForm.test.ts`。覆盖原子入队、上游未关闭时保持活动、无结果成功口径、平台隔离与冷却、部分预览保留、迟到结果裁决、独立归属、连接恢复、未来设置与轮次快照、历史保留及前端恢复/报告错误反馈。

文档验证命令：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/technical/validate_docs.py`。该脚本的 `documentation_date` 固定为 2026-09-24；本次实际校验日期为 2026-09-25。OpenAPI 在三个新路由可见后从临时应用对象生成，未启动 lifespan；本次评审修复保留 OpenAPI 和 DDL 正文，只更新 DDL 源码指纹及 manifest。校验仅在内存 SQLite 重放结构；Mermaid 检查围栏、图类型及表引用，未做视觉渲染。
