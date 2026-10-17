# 三条检索链路稳定性评审与实施记录

2026-09-25～26

范围：Google Maps、企业官网、社媒检索；优先稳定性和联系数据质量。技术实现与边界见[技术评审](../technical/search-stability-review.md)。

本轮已完成代码评审、修复、回归和本地部署。审计阶段使用只读连接检查现有工作项日志与全库联系人；部署阶段备份后归档明确无效记录。没有为了审计触发真实平台批量查询。

## 修复结果

| 链路 | 已修复问题与行为 |
|---|---|
| Google Maps | 底层快速重试关闭、导航间隔至少 5 秒；补读退出前日志；访问限制不会被“no captcha”误消除；定位服务冷却共享，坐标明确不匹配时使用文字方案 |
| 官网 | robots、重定向和底层重发均计入间隔与预算；按主机持久化暂停/冷却；HTTP 200 验证页在提取前停止；共享限制迁移可回滚，继续操作不跳过冷却 |
| 社媒 | 手动与市场任务共用平台限制，社媒官网发现共用主机限制；保存部分账号/帖子资料；SearxNG 采用服务要求的更长等待时间；平台恢复不会误清其他主机限制 |
| 联系人 | 排除空菜单链接、订阅/购物表单、整页外层表单、示例/坏邮箱；招聘与隐私等非获客用途排除出可用数量/导出；避免个人邮箱姓氏、真实留言表单和空邮箱采购归类误伤 |

三条链路分别评审并交叉检查。最后发现并补回归的边界包括：实际 TCP 发送时的共享间隔、HTTP 200 验证页、Retry-After 延长退避、手动恢复同时处理平台/主机限制、旧官网 gate 迁移原子性，以及联系人规则的误伤。

## 最终验证

| 验证 | 结果 | 证据 |
|---|---|---|
| `.venv/bin/python -m pytest -q --tb=short` | **957 passed，44 subtests passed**，47.37 秒 | [后端日志](search-stability-evidence/backend-tests.log) |
| `cd frontend && npm test` | **116 passed** | [前端日志](search-stability-evidence/frontend-tests.log) |
| `cd frontend && npm run build` | TypeScript 与 Vite 构建通过 | [构建日志](search-stability-evidence/frontend-build.log) |
| Maps 二进制能力与补丁检查 | `local-maps-stability-v1`；快速重试 0、详情 reload 关闭、导航间隔 5 秒；二进制 SHA 与构建记录一致 | [构建证据](search-stability-evidence/maps-build.json) |
| `docs/technical/validate_docs.py` | 117 个源文件、106 个文档链接、52 个业务接口、38 张表；完整性通过 | [文档校验](../technical/validation.json) |
| 本地部署检查 | API、任务详情、企业库、社媒连接接口均返回 200，后台 worker 在线 | [部署记录](search-stability-evidence/deployment.json) |

后端仅有两项依赖弃用提示（Starlette/httpx 与 AnyIO 别名），没有失败或跳过项。Mermaid 校验为结构检查，未声称逐图渲染。

## 历史数据处理与部署

2026-09-26 09:46（Asia/Shanghai）停止旧服务，建立 SQLite 备份，再运行迁移与联系人重分类：

- **633 条**明确无效记录标记为 `invalid`，覆盖 6 家企业；原始记录与证据未删除，修正原因写入变更历史。
- 195 家企业、2,925 条原始联系人记录、2 个市场任务、2 个运行轮次保持不变，所有原记录 ID 均保留。
- 去重、用途过滤后的可用联系人统计为 420。该数量不等于邮箱投递验证结果，也不等于采购人员数量。
- Facebook、Instagram、X 的本地 Cookie 文件在部署前后逐文件校验一致。仅证明本地登录资料保留，未重新登录平台验证会话有效期。
- 数据库 integrity_check 为 `ok`，无外键错误。
- 备份：`data/backups/backup-before-search-stability-deploy-20260926T014615Z.sqlite3`；精确路径同时记录于部署证据。

已重启 API 与 worker，并刷新用户原有任务页，确认显示“API 已连接 / 后台采集在线”。抽查 Absolute Apparel 企业详情，332 条误识别表单不再列入联系人列表，保留一项补充联系页面及原始修正历史。随后返回用户原有市场任务页。

## 验证范围

回归使用临时数据库、模拟 HTTP/浏览器子进程、虚拟时钟；不新增真实三平台批量抓取，不发送邮件或社媒消息。已受目标站点限制的历史任务不会仅因发布新版本就被改写为成功；需要登录、验证或处理站点限制的记录继续保留相应状态。低频、退避和恢复规则降低重复访问压力，不能保证平台永不限制访问。
