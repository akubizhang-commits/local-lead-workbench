# 市场任务内的社媒检索实施计划

> For agentic workers: implement the approved design with independent backend/frontend work, then integrated verification. User approved execution in this thread.

**Goal:** 企业发现后，官网与社媒各自排队并行推进，在市场详情统一展示真实进展。

**Architecture:** 保留已有社媒采集和证据模型，用关联表把社媒运行挂到市场轮次。三个固定执行通道分别处理地图、官网、社媒；社媒通道全局单并发，各平台独立低速等待或人工恢复。历史数据不自动触发采集。

**Tech Stack:** Python/FastAPI/SQLite/asyncio，React/TypeScript，已有浏览器会话。

**Spec:** 当前会话已批准的并行资料补全设计；`docs/technical/task-workflow-progress.md`。

## 约束与评审重点

- 保留已有登录态、业务数据、正在运行的地图和官网工作；不在真实企业上做批量采集测试。
- 市场统计按企业去重；未登录、冷却、排队不计执行结束；正常无结果是成功执行。
- 暂停/取消阻止迟到结果和继续扩展；单企手动任务不被市场控制误伤。
- 同轮账号去重、迟到官网账号增量入队、拒绝联系和不采用账号保持有效。
- 旧客户端和历史市场默认不启用；新表单默认启用并可选择平台和帖子。

## 实施与接口

- [x] 存储与状态机：新增 `task_social_settings`、`market_social_runs`、`social_platform_gates`，封装于 `market_social.py` 和 `social_policy.py`。在企业入库、官网补全同事务入队，复用社媒工作表，保留单企20家限制而允许市场逐企追加。
- [x] 输入输出：Task增加 `auto_social`、`social_platforms`、`social_read_posts`。`PATCH /api/tasks/{id}/social-settings` 只更新后续设置；`POST /api/tasks/{id}/social` 为当前活跃轮次补充社媒，若已结束则新建仅社媒轮次，不改旧轮次。`POST /api/social/platforms/{platform}/resume` 恢复对应平台待执行队列，不能提前跳过冷却。
- [x] 后台：增加可过滤阶段的原子领取，地图/官网/社媒通道独立执行；整段 worker 生命周期持有恢复锁，退出时取消并等待全部通道结束。并发测试使用 asyncio 事件，不访问真实平台。
- [x] 社媒流水线：企业名称站内搜索 → 账号基础资料 → 可选近期帖子；同步读取官网新增账号。登录/验证/会话占用仅暂停对应平台，网络异常有限退避，成功项保留。
- [x] 进度：workflow.stages追加 `social`，主计数为企业。workflow.social提供 child run_id、platforms（按平台进度、原因、下次时间和是否人工恢复）、current、accounts_found、posts_found。`social_only` 区分后补轮次。完整逐项报告复用 `/api/social/runs/{id}`。
- [x] 前端：创建/市场设置增加社媒选项；资料补全两条分支清楚标注并行；显示平台等待/登录/恢复入口、报告及账号成果；已有市场可显式补充。
- [x] 验证：隔离数据库覆盖原子入队、去重、无官网、断点恢复、登录隔离、暂停/取消、冷却、全部阶段完成判断和历史兼容；前端覆盖表单提交、平台计数、展开/恢复按钮和缺失状态。构建、全量回归、文档校验、浏览器只读验收后更新运行服务。
