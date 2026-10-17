# 任务详情：流程与进度

2026-09-25。

市场详情页默认展开“任务流程与进度”，从 task activity 接口的 workflow 读取数据，每5秒刷新。未选轮次时显示最新轮次；选定轮次时只显示该轮次。复核列表分页和状态筛选不影响流程统计。企业独立复核仍使用原详情视图。

流程为 1 规划检索范围、2 Google Maps 检索、3A 官网资料补全 / 3B 社媒检索与补全（并行）、4 结果汇总。社媒开启时追加 `key=social` 阶段；仅社媒轮次只显示社媒和结果汇总，不生成零 Maps/官网阶段。规划/地图/官网沿用 work_items，社媒从关联子运行的 social_work 按企业聚合。processed 包含成功和已结束异常，成功单列，避免用 100% 已处理暗示全部成功。

查询、企业和账号会随执行增加；Maps/官网上游仍有 pending/running 时，即使当前社媒队列暂空也不能认定本轮全部结束。不展示固定总完成百分比或预计完成时间；汇总是轮次状态和实际剩余工作的投影，不虚构独立汇总工作项。具体父子终态规则见[市场社媒集成](market-social-integration.md)。

当前对象从运行中工作项读取：地图展示 query，官网展示企业名称，社媒展示平台、企业和操作。`current` 可同时包含官网与社媒动作，前端保留完整文案，不强制只选一项。没有运行项时显示平台间隔、登录/会话/配置等待或后台调度等待。暂停、取消优先展示控制状态；终态标签不能覆盖真实存在的社媒 pending/running。后台离线时覆盖陈旧的“正在处理”。地图及社媒当前门控不用于伪造历史轮次正在等待。

累计时长使用轮次创建至结束/当前时间，明确包含等待和暂停。网页/联系记录来自官网工作项累计 summary，联系记录不是去重后有效联系人。通常企业数来自本轮 Maps observations 的去重公司 ID；仅社媒轮次改为补充范围企业数。未知总量显示待确定，不显示 0/0。社媒账号、帖子为独立成果统计，既不替代企业数，也不等于已确认采购人员。

`GET /api/tasks/{id}/activity` 的 `workflow` 保留 run_id,status,created_at,elapsed_seconds,current,stages,companies_found,website_pages,contacts_found,website_links_remaining,auto_enrich,maps_wait,updated_at，以及已有地图恢复字段；新增 `social_only:boolean` 和 `social:object|null`。未找到指定本市场轮次或尚无轮次时为 null。该接口只读，不启动补充或重放历史。

| 社媒字段 | 口径 |
|---|---|
| `stages[].key/title` | `social` / `社媒检索与补全` |
| `total/processed/pending/running/succeeded/partial/failed/blocked` | 全部按去重企业计数，同企业多项只占一个当前企业状态；processed 为执行已结束企业 |
| `no_results`（可选） | 正常无结果企业，已包含在 succeeded 中，不重复累加 |
| `social.run_id` | 子社媒运行 ID，与 workflow.run_id 的市场父轮次区分 |
| `social.current/accounts_found/posts_found/read_posts` | 当前社媒动作、账号数、帖子数、本轮帖子选项 |
| `social.platforms[]` | `platform` 为 facebook/instagram/x；该平台企业计数、state、reason、next_attempt_at、manual_required |

平台计数包含 total/processed/pending/running/succeeded/no_results/failed/blocked，服务端可附 partial；同一企业可出现在多个平台，平台数不能相加当市场去重总数。完整 `/api/social/runs/{child_id}` 报告的 summary 则按处理项计数，一个企业可有多项。

企业聚合同时检查 `result.partial`：即便原工作项 state=succeeded，只要仍有部分失败就归入 partial，父轮次也保留异常。读取到的有效部分预览可在等待重试时展示，但该企业仍是 pending/running，不能提前计为全部结束。

平台 state 包括 running、waiting_login、waiting_recovery、waiting_session、waiting_configuration、pending、completed、needs_attention、paused、cancelled。`pending` 配 future next_attempt_at 也可能是正常访问间隔；需结合原因显示。正常无结果是成功，登录/冷却/排队不是已结束的失败。`social` 缺失时保留阶段可读统计并提示平台明细尚未提供，不编造账号数或恢复操作。

后端实现：leadworkbench/task_workflow.py；前端组件：frontend/src/TaskWorkflow.tsx。测试覆盖异常与成功分离、跨页筛选不影响计数、历史轮次选择、全局等待、暂停、后台离线、官网自动补全关闭。

### 规划兜底的完成口径（2026-09-25）

缺少范围坐标时，只要生成了可执行的地区文字查询，规划即为 `completed`，工作项为 `succeeded`；`mode=text_only`，保留棕色坐标缺失说明。坐标定位失败属于策略降级，不再单独导致整轮任务部分完成。真实规划错误（例如不支持的企业类型未能展开）、检索失败、受阻和覆盖能力耗尽仍按原规则处理。

`workflow.stages` 包含 `mode` 和 `details`。正常兜底显示成功进度条和“已生成文字检索方案”；部分完成、失败及受阻仍使用提示色。进度表示执行结束比例，不代表查到了地区所有企业。旧轮次仅对已有可执行文字查询且没有其他规划问题的记录修正规划状态；保留原因和原结束时间，重新汇总真实子任务结果。

### 地图等待与重试（2026-09-25）

地图阶段将 pending 拆为“待重试”和“等待执行/恢复”，避免把未执行项计为受阻或执行结束。展示下次自动尝试时间（后台在线、队列允许时）、原因及有限重试次数；重试明细默认折叠。当前保护状态和历史轮次隔离。具体状态机、接口字段、旧数据修复及验证见 [地图低速与恢复策略](maps-pacing.md)。

### 紧凑进度总览（2026-09-25）

首屏优先展示本轮企业、官网已读页数、提取联系记录和当前操作；联系记录明确标注“原始记录，未去重”。阶段采用纵向紧凑列表，3A/3B 明示并行，每行展示执行结束数/当前总量和剩余数量；社媒账号/帖子另列成果。仅社媒轮次省略官网统计，展示补充企业、账号、帖子。地图等待显示向上取整的分钟数；到期显示等待调度，不承诺准点执行。结果汇总保留当前市场企业库入口；有 pending/running 不因终态标签显示“本轮已结束”，已结束但有异常显示“有需关注项”。

进度条用绿色表示成功、棕色表示部分完成/失败/受阻、灰色表示未结束的工作；同一行保留可读取的数值与状态，避免只靠颜色解释结果。未知总量显示“任务数待确定”，不展示0/0进度条。没有新的整体百分比或预计完成时间。

“需关注/待重试/查看明细”按钮支持鼠标悬停、键盘聚焦预览简要说明，点击或触屏展开阶段内的详细统计、等待原因、下次尝试时间和重试次数。Escape收起说明或关闭提示，自动刷新保留已展开状态，切换轮次重置展开状态。完整地图重试历史、进度口径和采集时间放在底部折叠区。需要人工恢复时，操作入口与原因始终显示，不藏在提示中；冷却、忙碌或后台离线时禁用恢复按钮。

桌面采用阶段/进度/下一步三列，窄屏上下排列。沿用 `WorkflowHint.tsx` 的聚焦/悬停、点击展开、Escape 收起；自动轮询保留展开，切轮次重置。社媒平台操作与原因保持可见，“平台明细”展开企业计数，完整报告再次展开才读取子运行，不在页面加载时请求报告或启动采集。`WorkflowSocial.tsx` 处理平台展示、恢复和报告，`TaskWorkflow.tsx` 组织阶段。原紧凑样式保留，市场社媒的数据与状态扩展见[集成设计](market-social-integration.md)。

轮次详情 RunCard 将 `kind=social` 标为社媒轮次，只展示 `progress.social_done/social_total` 的企业进度；通用 done/total 也按此口径投影。市场顶层 coverage/market_stats 继续引用最近 discovery，最新社媒轮次不清空发现统计。企业社媒报告根据 `parent_run_id/task_id` 导向父市场控制；内部 claim_token 不在报告 API 返回。

### 阶段结果与官网原因汇总（2026-09-26）

大步骤不再把所有异常统称为“有未完成项”。有实际排队、运行、登录或冷却等待时继续展示相应状态；本次执行结束后，全成功为“已完成”，有部分结果和异常为“部分已完成”，全部未取得结果为“未取得结果”。官网部分结果以已读取网页为依据，不能仅凭 `state=partial` 推断已取得结果。扫描成功但没有发现联系方式也不等于执行失败。暂停、取消、0/0 和未开启补全的情况不改称已完成。

轮次本身仍保留真实 `status`，不修改执行队列、失败原因、重试策略或历史时间。有结果的 partial 轮次提示“本轮已结束，已取得的结果已保存；部分来源未能获取资料”，不要求用户将所有外部网站都重试成功。

`workflow.stages[key=refresh].website_summary` 是新增只读字段：

| 字段 | 口径 |
|---|---|
| `total / processed / pending / running` | 当前选定轮次的官网企业数；不包含独立复核和其他轮次，不随下方分页/状态筛选改变 |
| `with_pages / without_pages` | 仅已结束企业中读到网页／未读到网页的数量；互斥且合计为 processed，正在分批扫描的企业不会提前算作已结束 |
| `files_unparsed_companies` | 已结束企业中存在未解析文件的企业数；只是补充说明，不自动计为访问失败 |
| `reasons[]` | `code,label,companies,with_pages,without_pages`；每家异常企业按当前主要原因只计一次，同一企业的多页/多次失败不会重复计数 |

主要原因包括访问拒绝或验证、限流或服务异常、证书、跳转、扫描范围/预算、超时、网络、网页404/410、内容过大、内容未解析、robots规则及未明确原因。优先使用当前访问限制，其次读取未恢复页面的结构化记录；已经 parsed 的页面不再用其旧日志和旧警告归因。仅有老版 message 时使用保守的文本分类，无法确定的原因显示“其他读取问题”，不推断永久不可访问或账号/IP被封。

官网“官网检索明细”沿用悬停/聚焦预览和点击展开，先展示已处理范围、读到／未读到网页的企业数，再列原因及其中仍有部分网页结果的数量。未解析文件另列，提示重试不能保证外部网站恢复。原始企业日志仍在资料复核详情页保留。实现：`leadworkbench/website_workflow.py`，回归：`tests/test_website_workflow_summary.py`。
