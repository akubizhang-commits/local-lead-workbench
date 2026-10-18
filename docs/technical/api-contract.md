# 当前 API 协议

状态：已完成代码核对与模块交叉评审；本轮未修改业务实现。本文记录当前源码行为，不把 `docs/product/` 的目标设计写成已实现接口。

依据：`leadworkbench/api.py` 的全部路由、输入模型与中间件，以及 `db.py`、`market_store.py`、`social_store.py`、`social_discovery_store.py` 的返回值。配套附件为本目录 `openapi.current.json`、`schema.current.sql`；数据库语义见本目录 `database.md`。DDL 文件头保留本次核心源码 SHA-256，可用于检查后续漂移。

## 1. 范围与附件边界

- 2026-09-25 快照有 **52 个 `/api/` 方法-路径组合**，另有一个 GET 前端兜底路由。原生 OpenAPI 共 45 个路径、53 个操作；条件挂载的 `/assets` 不在其中。数量按同次源码/快照校验，不代表逐端点行为全部验收。
- `openapi.current.json` 是临时 `create_app(temp_dir).openapi()` 的原始输出，未人工补造响应模型。它精确记录当前框架声明的请求模型和参数，但不能完整表达自定义验证器及跨字段业务规则。API 没有声明业务 `response_model`，故大部分成功响应 schema 为空；中间件 token、400/401/403/409/413、文件导出 MIME 也未完整反映在 OpenAPI 中。**生成OpenAPI不代表响应已完成类型验收**；本文的临时示例验证也不等于全响应类型验收。集成时必须同时阅读本文。
- 应用禁用 `/docs`、`/redoc` 和在线 OpenAPI 路由；附件是离线文档，不意味着 `/openapi.json` 可下载协议。
- 创建采集任务只持久化排队，由独立 worker 执行。本文编制未启动 worker、探测搜索服务、打开登录浏览器、运行采集或发送任何消息。
- **没有邮件发送、社媒发送、统一人员关联、投递回执、回复同步 API。** `social/outreach` 是人工记录接口；`state=contacted/replied` 不证明真实触达。

## 2. 本地访问、认证与通用格式

源码：`api.py:201–257`。

1. 默认允许 URL hostname 为 `127.0.0.1`、`localhost`、`::1`；`create_app(..., allowed_hosts=...)` 可覆写。此检查读取请求 URL/Host，不是对远端 IP 的认证；服务监听地址仍由启动方式决定。
2. 若存在 `Origin`，其 `netloc` 必须与请求 `Host` 字符串相同；代码不比较 scheme，也不是通用 CORS 授权。`Sec-Fetch-Site: cross-site` 被拒绝。
3. 先 `GET /api/session` 获取 `{"token":"..."}`，后续所有 `/api/` 请求携带 `X-Local-Token`。session 仅豁免 token，仍受本地边界和请求大小检查。没有账号密码、JWT、OAuth 或角色权限。
4. token 由每个应用实例启动时随机生成，重启后失效。没有持久化 token 或刷新接口；401 后客户端可重新获取 session。不同应用进程不共享 token 或 API 锁。
5. 输入 body 为 JSON。所有 `Input` 模型禁止额外 body 字段；不是全局严格类型模式，按 Pydantic 的实际类型转换处理。未声明的 query 参数没有同等 `extra=forbid` 保证。
6. 中间件仅检查 `Content-Length`：非数字或大于1,000,000返回413。缺失时按0处理，不能将其描述成完整的流式 body 限额。
7. 通常成功返回 HTTP 200，包括创建、任务排队、人工记账；没有201/202约定、统一 `data` 包装、ETag 或 `Idempotency-Key` 支持。
8. 一般响应有 `Cache-Control: no-store`、`nosniff`、`no-referrer` 和 CSP；中间件提前拒绝的响应在这些头设置之前返回，不保证含全部头。

协议示意（占位符需替换；不是已执行的采集指令）：

```http
GET /api/session HTTP/1.1
Host: localhost:8000

HTTP/1.1 200 OK
Content-Type: application/json

{"token":"<当前实例token>"}
```

```http
GET /api/company-page?page=1&page_size=50&contact=procurement HTTP/1.1
Host: localhost:8000
X-Local-Token: <当前实例token>
```

端口由启动配置决定，上例8000不是协议固定端口。

## 3. 完整端点索引

下表路径保留 `/api` 前缀。“无”表示不接收业务 body；读写类型并不代表可以在只读评审中调用。

### 3.1 会话、概览、市场与常规任务

| 方法 | 路径 | 输入 | HTTP 200 返回 / 注意 |
|---|---|---|---|
| GET | `/api/session` | 无；免token | `{token}` |
| GET | `/api/overview` | 无 | 企业、联系人、任务、待审、运行/排队计数，`worker`，`source_status`；检查二进制存在不等于来源实测成功 |
| GET | `/api/regions` | `parent_id?: string`，默认null | 静态地区数组 `{id,name,parent_id,kind}`；默认国家层，不是完整世界地区库 |
| GET | `/api/tasks` | 无 | `Task[]`，无分页 |
| POST | `/api/tasks` | `TaskInput` | `Task`；同市场 identity 返回已有定义 |
| POST | `/api/tasks/{id}/runs` | 无 | `Run`；新建或复用活动轮次，不执行采集 |
| PATCH | `/api/tasks/{id}/social-settings` | `TaskSocialSettingsInput` | `Task`；仅保存后续自动社媒设置，不改本轮快照、不执行补充 |
| POST | `/api/tasks/{id}/social` | `MarketSocialRunInput` | 市场父 `Run`；附加活动轮次或创建仅社媒轮次，保留历史 |
| GET | `/api/runs` | 无 | `Run[]`，最新200条 |
| GET | `/api/runs/{id}` | 无 | `Run`；市场轮次带coverage，无work_items数组 |
| POST | `/api/runs/{id}/relocate` | 无 | `Run`；仅市场检索轮次的 text_only 计划可“重试定位并补查”，规则见下文“2026-10-18：P0 修复”；路由注册在 `{action}` 之前 |
| POST | `/api/runs/{id}/{action}` | path action=`pause/resume/cancel` | `Run`；不允许的状态转换409 |

### 3.2 企业库、审核、变更与导出

| 方法 | 路径 | 输入 | HTTP 200 返回 / 注意 |
|---|---|---|---|
| GET | `/api/companies` | `q/status/type/category/excluded`，默认空串/hide | `Company[]`；不分页、无数量上限 |
| GET | `/api/company-page` | 见§4分页协议 | `{items,total,page,page_size,excluded_total}` |
| GET | `/api/company-categories` | `market_id?`、`excluded=hide/only/include` | `[{category,category_key,count}]`，按 count 降序，≤200 |
| GET | `/api/exclusion-rules` | `market_id?` | 规则数组（带 `matched`）；传市场时为全局+该市场规则 |
| POST | `/api/exclusion-rules` | `ExclusionRuleInput` | 新建 201；同作用域同值返回已有规则 200 且 `reused:true` |
| DELETE | `/api/exclusion-rules/{id}` | 无 | 204；不存在 404 |
| POST | `/api/companies/{id}/contacts` | `ManualContactInput` | 最新 `detail`；revision 不符/拒绝联系/同邮箱/同指纹 409 |
| POST | `/api/companies` | `CompanyInput` | `Company`；每次人工新增，不去重 |
| GET | `/api/companies/{id}` | 无 | `{company,contacts,observations,changes,social_links,related_locations}` |
| PATCH | `/api/companies/{id}` | `CompanyPatch` | 更新后`Company`；必须revision |
| POST | `/api/companies/batch-status` | `BatchStatus` | `{updated: N}`；整批原子校验revision |
| POST | `/api/companies/{id}/refresh` | 无 | `Run`；无官网400、拒绝联系409 |
| GET | `/api/reviews` | 无 | 待审核`Review[]`，按创建时间倒序；无分页。同企业同字段同值的观察合并为一条审核，每条含 `evidence_count`（来源数）与 `evidence_items`（最近≤5条 `{source_url,evidence,observed_at}`） |
| POST | `/api/reviews/{id}/decision` | `{decision:"accept"或"reject"}` | `{ok:true,auto_closed:N}`；联系人变化 reject 另返回 `kept_previous:true`，accept 时当前值已变返回 409 并作废该建议（N 为同时自动关闭的同值待审核数）；已处理409；重复企业候选不允许accept覆盖另一企业 |
| GET | `/api/changes` | 无 | 变更数组，最新500条，含`company_name` |
| GET | `/api/exports` | `format=csv/xlsx`、`layout=companies/entries`及§4筛选；无page | 文件二进制；默认csv+companies，`leads.*` / `contact-entries.*`，详见§8与 P0 节 |

### 3.3 社媒配置、浏览器与任务

| 方法 | 路径 | 输入 | HTTP 200 返回 / 注意 |
|---|---|---|---|
| GET | `/api/social/settings` | 无 | `{searxng_url,configured,platforms,message}` |
| PUT | `/api/social/settings` | `SocialSettingsInput` | 同上；保存配置，不验证服务可达 |
| GET | `/api/social/connections` | 无 | 公开连接配置、`youtube_key_configured`、`dependencies`、`sessions`；不返回API key |
| PUT | `/api/social/connections` | `SocialConnectionsInput` | 同上；任一专用登录窗口打开时409 |
| POST | `/api/social/browser/{platform}/open` | platform=`instagram/facebook/x`；无body | `{status:"open",message}`；启动专用浏览器，有进程副作用 |
| POST | `/api/social/browser/{platform}/close` | 同上 | `{status:"closed",message}`；关闭窗口并保留专用目录，不验证登录有效性 |
| POST | `/api/social/probe` | 无 | `{status,message}`；实际查询SearXNG，业务受阻仍可HTTP 200 |
| POST | `/api/social/runs` | `SocialRunInput` | `SocialRun`，含items和summary；仅入队 |
| GET | `/api/social/runs` | `company_id=""` | 最近30条，不含items；company_id通过work关联筛选 |
| GET | `/api/social/runs/{id}` | 无 | `SocialRun`，含items和summary |
| POST | `/api/social/runs/{id}/cancel` | 无 | `{ok:true}`；已结束轮次不改为cancelled |
| POST | `/api/social/runs/{id}/retry` | 无 | 新的或复用活动`SocialRun`；不在原轮次上续跑 |
| POST | `/api/social/platforms/{platform}/resume` | platform=`facebook/instagram/x/tiktok/youtube/pinterest/bluesky/searxng`；无body | `{platform,state,message}`；恢复该来源保留队列，冷却未到期409；searxng为搜索服务 |

### 3.4 社媒归属与人工跟进

| 方法 | 路径 | 输入 | HTTP 200 返回 / 注意 |
|---|---|---|---|
| GET | `/api/companies/{id}/social` | 无 | `SocialDetail`，见§5 |
| POST | `/api/companies/{id}/social/sync` | 无 | `SocialDetail`；把已有公司链接/观察同步到社媒实体，有DB写入但无网络抓取 |
| POST | `/api/companies/{id}/social/links` | `{url,evidence?}` | `{platform,kind,url,label,reason,id}`，不是完整Entry；候选归属 |
| POST | `/api/companies/{id}/social/{entity_id}/decision` | `SocialDecisionInput` | `{ok:true}`；针对企业-实体关联的revision |
| POST | `/api/companies/{id}/social/outreach` | `SocialOutreachInput` | `{ok:true}`；追加人工事件，不返回事件ID，不发送 |

### 3.5 计划、设置、备份与前端

| 方法 | 路径 | 输入 | HTTP 200 返回 / 注意 |
|---|---|---|---|
| GET | `/api/schedules` | 无 | `Schedule[]`，按next_due_at排序 |
| POST | `/api/schedules` | `ScheduleInput` | `Schedule`；创建新计划，不按目标去重 |
| PATCH | `/api/schedules/{id}` | `{enabled:boolean}` | `Schedule`；无revision、后写覆盖 |
| GET | `/api/settings` | 无 | `{data_dir,version:"0.1.0",network_mode:"direct-public",paid_services:false,ai_enabled:false}` |
| GET | `/api/backups` | 无 | `[{name,created_at}]`；按文件名逆序，日期来自文件mtime |
| POST | `/api/backups` | 无 | `{name,created_at}`；创建SQLite备份 |
| POST | `/api/backups/restore` | `{name}` | `{ok:true}`；实际恢复，有锁和活动任务检查，见§9 |
| GET | `/{path:path}` | path | 前端index文件；`api/`前缀未匹配时404 `接口不存在`；未构建时503文本 |
| 挂载 | `/assets` | 静态路径 | 仅构建assets目录存在时挂载；不是业务API，不计入44项 |

没有 DELETE、任务定义修改、独立联系人 CRUD、发送或入口级拒绝联系端点。不存在的HTTP方法和路径由路由层处理，不统一转为业务400。

## 4. 输入、分页与筛选

### 输入模型

字段完整约束以原生OpenAPI和模型验证器共同为准；OpenAPI不能表达所有跨字段规则。

| 模型 | 当前字段与附加规则 |
|---|---|
| TaskInput | `mode="market"`；name兼容可选、最长120且dump排除，服务端生成名称；country默认US，去空格转大写、两个ASCII英文字母；state/city必填1–120；keywords默认[]、最多30，每项去空格后去重，空白或原长度>160拒绝；company_types默认[]、最多8，枚举wholesale/retail/import；二者至少一个非空；auto_enrich默认true。兼容`limit>=1`但dump排除，实际市场limit=0，不控制企业总量；社媒选项见下行 |
| TaskInput / TaskSocialSettingsInput 的社媒选项 | `auto_social=false`、`social_platforms=["facebook","instagram","x"]`、`social_read_posts=false`；平台限三枚举、去重、最多3项；auto_social为true时至少一个平台。新表单显式传true，不改变后端/旧客户端false默认 |
| MarketSocialRunInput | `social_platforms` 与 `social_read_posts` 同上；显式补充必须至少一个平台，不包含未来自动开关 |
| CompanyInput | name必填1–200；website必填1–4096，经`normalize_url`验证和规范化，不做真实访问；city默认""最长120；country默认US最长80，**没有TaskInput的国家代码验证器** |
| CompanyPatch | revision必填≥1；fields默认{}，仅name/address/phone/website，值最长4096；name不能纯空白、非空website规范化；notes可省略/null/文本≤10000；status可null或四种企业状态；business_types可null或≤20项，每项去空格非空≤80并去重。null表示不修改对应可选字段；清空用""或[] |
| BatchStatus | companies为1–200个`{id(1–100),revision≥1}`；禁止重复id；status为企业四状态之一 |
| SocialSettingsInput | searxng_url默认""最长300。空串关闭；仅本机http、端口>1023、路径空/`/`/`/search`，禁止凭证/query/fragment；保存成`/search` |
| SocialConnectionsInput | reader=http/browser默认http；instagram_reader=browser/instaloader默认browser；proxy_url默认""最长300，仅本机http/socks5和端口≥1024，无凭证/query/fragment；max_posts默认3、1–5；youtube_api_key可null、最长300。PUT按实际提供且非null字段合并，省略不重置；key空串清除，null不改 |
| SocialRunInput | company_ids默认[]、最多20；pilot/read_only/platform_search/profile_only默认false。pilot时忽略显式id，按8已有链接/8仅官网/4无官网及补位最多20；最终至少1家且不可已合并/拒绝联系。profile_only隐含read_only；profile_only或read_only不能与platform_search同时为true；read_only要求整批至少一家存在未排除账号 |
| SocialLinkInput | url必填1–4096，必须能分类为支持的平台账号/帖子/集合；evidence默认""最长3000；不是抓取请求 |
| SocialDecisionInput | status=confirmed/candidate/excluded；scope=brand/location/employee/third_party/unknown默认unknown；revision必填≥1；confirmed+unknown业务400 |
| SocialOutreachInput | entity_id必填1–64；state=draft/contacted/replied/closed；note必填1–5000，业务层另拒绝纯空白并trim；要求企业非拒绝联系、关联status=confirmed |
| ScheduleInput | task_id/company_id默认null，必须恰好一个真值；kind=discovery/refresh且分别对应task/company；interval_months默认3、1–24；timezone默认Asia/Shanghai且ZoneInfo有效；enabled默认true |
| Decision / Toggle / Restore | 分别为`decision=accept/reject`、必填enabled布尔、name必填1–200；restore还要求纯文件名、`.sqlite3`后缀、已有非符号链接备份 |

### 企业分页

`GET /api/company-page`：

| query | 默认 / 合法值 |
|---|---|
| q/status/type | ""；q匹配名称、城市、官网、地址、国家及所属市场州省，使用SQL LIKE，`%`/`_`未转义；status为精确匹配字符串，查询端不验证四状态枚举；type支持wholesale↔wholesaler、retail↔retailer、import↔importer |
| market_id/run_id | ""；若提供必须存在；run可推导market；两者不一致400；仅market时选择最新轮次 |
| segment | "" / new / existing / changed / not_seen；正常市场分组需market上下文 |
| contact | "" / email / procurement；email指剔除采购重复邮箱后的“其他邮箱”，不是所有含邮箱记录 |
| sort | newest / procurement / email / updated，默认newest |
| page/page_size | 1/50；page≥1，API page_size=1–200。Store内部上限1000不能作为HTTP上限 |

使用offset分页，返回请求页号，越界页`items=[]`，不自动夹到最后页；没有cursor或固定快照。排序带id作为最后排序项，但并发更新可能改变跨页结果。total与items为同一连接的分别查询，不声明跨请求一致性。

`new/existing`互斥；changed是独立布尔；not_seen表示本轮未遇见而非停业。历史轮次筛选仍展示企业当前字段，不是时点快照。指定非市场refresh run会按observations.run_id查企业；不要将segment用于这种请求，当前该分支未应用segment。

列表的`contact_count`是投影去重后的可用联系记录数，`procurement_count`含采购人员/部门入口，`social_count`含未排除候选账号；它们不是“已确认人数”，不能相加当总人数。`do_not_contact`默认仍可在企业列表中看到，但导出排除。

## 5. 实际响应结构

以下为字段协议说明；没有静态响应模型强制过滤，新增字段可直接出现在API响应中。ID通常为32位十六进制文本，但客户端应按不透明字符串处理。时间通常为带UTC偏移的ISO文本；缺失值可能是null或空串，不强制统一。

| 对象 | 字段 |
|---|---|
| Company | 表字段`id,name,address,phone,website,city,country,business_types[],status,notes,revision,source_kind,created_at,last_checked_at,overrides[],social_links[],merged_into`；追加`contact_count,manual_checked_at,operating_status,operating_source_url,operating_observed_at`。列表另有`email_count,procurement_count,social_count`，市场列表另有`market_segment,market_changed:boolean,first_seen,last_seen` |
| Task | 表字段`id,name,country,state,city,keywords[],company_types[],limit,auto_enrich:boolean,created_at`；`latest_run`可null；市场增加`mode:"market",market_stats,coverage`；社媒设置投影 `auto_social:boolean,social_platforms:string[],social_read_posts:boolean`，历史缺失按false/三平台/false读取 |
| Run | `id,task_id,kind,company_id,status,progress:{...},message,error,created_at,finished_at,snapshot:{...}`；GET列表及单条额外返回company_name（关联企业名称，无关联时为null），资料复核卡片显示名称并链接企业详情；市场GET/控制返回可带coverage；start直接返回基础行，**不保证有coverage** |
| progress / coverage | progress含done/total/discovered/enrichment_done/enrichment_total；2026-09-24增加website_pages_checked/website_links_remaining（累计已解析页数/待扫描链接数），市场再含new/existing/changed/discovery_done/discovery_total/market_total。coverage含scope_label/planned/done/unresolved[]，有轮次时再有capability_exhausted/retryable，及可选bbox/attribution；未开始时字段更少 |
| Contact | 当前表字段加`adoption_status`；详情投影有`fields[{key,label,value,source_url,source_label,observed_at}]`、`sources[{source_url,evidence,observed_at}]`、`record_ids[]`、`outreach_category`。排除项可保留在详情；不要用“数组存在”推断可采用 |
| Observation / Change | observations返回原始表列；`value`仍可能是普通字符串或JSON编码字符串。changes含`company_name`及原始old_value/new_value，不统一解码 |
| Review | 原始列及company_name；contact类额外`contact`和`previous_contact`；duplicate_candidate额外`comparison:{left,right,same_website,website_group}`。old_value/new_value按kind解释 |
| SocialRun | `id,status,created_at,finished_at,company_ids[],sample[],items[],summary,platform_gates`；market 关联运行另含 `parent_run_id,task_id`，手动独立运行不带。platform_gates按涉及来源返回state/reason/manual_required/next_attempt_at，表示共享的当前访问状态。**移除 settings 与 items[].result.claim_token**，列表还删除 items。sample 条目含 id/name/stratum；items 含 id/run_id/company_id/company_name/stage/target/state/result对象/created_at/finished_at |
| 社媒summary | total/pending/running/succeeded/no_results/blocked/failed/cancelled/skipped/new_links；new_links为新企业-实体关联数，不等于新采购人员数 |
| SocialDetail | accounts/posts/excluded/ignored_links/outreach/search_queries/pages/do_not_contact/capabilities。posts也可能含collection；capabilities当前固定包含`auto_send:false` |
| 社媒Entry | social_entities列加company_social的status/scope/revision/decided_at、linked_company_count、last_observed_at和evidence[]；revision属于当前企业关联，不是全局账号版本 |
| 社媒pages | entity_id、match_signals及投影payload：status/message/fields/field_history/posts/content_sources/observed_at，可能有title/description/source_url/provider/content_observed_at。失败可保留旧内容，字段retained和各自时间说明来源 |
| outreach事件 | id/company_id/entity_id/state/note/created_at，查询时加company_name/url/label/platform；最多200条，包含共享已确认实体上其他企业的历史 |
| Schedule | 表字段id/task_id/company_id/kind/interval_months/timezone/next_due_at/enabled:boolean/anchor，追加waiting_reason |

`coverage.done`只数成功discovery任务；`progress.discovery_done`可含partial/failed/blocked已结束任务，数值不同不是重复企业。“完成”不表示全城企业找齐，也不表示联系方式通过收信验证。

## 6. 关键请求与响应示例

以下ID、名称、时间均为示例；标注“节选”的响应不是完整对象定义。

### 创建市场与排队

`POST /api/tasks`

```json
{"name":"洛杉矶服装批发","country":"US","state":"California","city":"Los Angeles","keywords":["apparel wholesaler"],"company_types":["wholesale"],"auto_enrich":true}
```

响应节选：

```json
{"id":"<market-id>","mode":"market","limit":0,"auto_enrich":true,"latest_run":null,"market_stats":{"total":0,"new":0,"existing":0,"changed":0,"not_seen":0},"coverage":{"scope_label":"","planned":0,"done":0,"unresolved":[]}}
```

`POST /api/tasks/<market-id>/runs`（无body），响应节选：

```json
{"id":"<run-id>","task_id":"<market-id>","kind":"discovery","company_id":null,"status":"queued","message":"等待本地 worker","error":null,"finished_at":null}
```

随后轮询`GET /api/runs/<run-id>`；没有WebSocket/SSE或长任务HTTP流。

### 分页与revision编辑

`GET /api/company-page?page=1&page_size=50&market_id=<market-id>&segment=new` 返回形状：

```json
{"items":[],"total":0,"page":1,"page_size":50}
```

`PATCH /api/companies/<company-id>`：

```json
{"revision":3,"fields":{"phone":"+1 555 0100"},"status":"verified","notes":"人工核对业务适配"}
```

成功返回Company且revision=4；`verified`意为“符合目标客户”，不代表企业认证、采购身份确认或邮箱已验证。旧revision再次提交：

```http
HTTP/1.1 409 Conflict
Content-Type: application/json

{"detail":"资料已更新，请刷新后重试"}
```

整批更新使用：

```json
{"companies":[{"id":"<company-a>","revision":4},{"id":"<company-b>","revision":2}],"status":"not_fit"}
```

响应`{"updated":2}`；任一revision不匹配则整批不修改。

### 社媒发现、归属与人工记账

`POST /api/social/runs`：

```json
{"company_ids":["<company-id>"],"platform_search":true}
```

新轮次首先排队facebook/instagram/x三项企业名搜索。响应summary初始可为：

```json
{"total":3,"pending":3,"running":0,"succeeded":0,"no_results":0,"blocked":0,"failed":0,"cancelled":0,"skipped":0,"new_links":0}
```

这是summary节选，后续发现候选可追加profile任务，总数会增长。`read_only:true`仅表示“读取已有账号、不搜索”，**不是数据库只读模式**；会持久化任务和读取结果。

`POST /api/companies/<company-id>/social/<entity-id>/decision`：

```json
{"status":"confirmed","scope":"brand","revision":1}
```

响应`{"ok":true}`；随后GET社媒详情取得revision=2。确认品牌归属不会建立人员关系或采购岗位。

`POST /api/companies/<company-id>/social/outreach`：

```json
{"entity_id":"<entity-id>","state":"draft","note":"待人工修改的产品介绍"}
```

响应`{"ok":true}`。重复POST会新增重复事件；改成contacted/replied同样只是人工记账。没有邮件主题、发送账号、附件、消息ID、幂等键或投递回执字段；规划中的发送准入表尚非此接口实现。

### 定时维护

`POST /api/schedules`：

```json
{"task_id":"<market-id>","kind":"discovery","interval_months":3,"timezone":"Asia/Shanghai","enabled":true}
```

返回Schedule，含服务端计算的anchor/next_due_at。按日历月推进，不是固定90天；需要worker运行。暂停的旧任务可使当期调度延后。计划不是发送计划。

## 7. 并发、幂等与状态规则

源码：`db.py:348–407,645–741`，`market_store.py:150–167`，`social_store.py:89–118`，`social_discovery_store.py:64–115,230–243`。

API实例通过`asyncio.Lock`串行处理`/api/`请求；跨进程依靠SQLite事务、业务锁及revision，不能据此承诺全局单请求。SQLite连接timeout/busy_timeout为15秒，重要写操作使用`BEGIN IMMEDIATE`。

| 操作 | 实际重复提交语义 |
|---|---|
| 创建市场 | identity由国家/州/城市/关键词集合/企业类型集合规范化得到；忽略name和auto_enrich差异，命中返回旧市场，不修改旧定义 |
| 创建普通轮次/官网refresh | 同kind+task/company在queued/running/paused时复用；blocked/partial/failed不在复用集合；完成后再次启动可新建 |
| 创建社媒轮次 | 同排序后的企业ID集合+执行签名，且queued/running时复用。签名含模式、SearXNG、连接配置；不同模式不会错误复用 |
| 市场显式补充 | 复用当前 queued/running/paused 父轮次及匹配的关联社媒配置；没有活动轮次则新建 kind=social。与手动独立社媒运行不相互复用；旧终态轮次不重开 |
| 社媒retry | 原轮次queued/running时报400；保留原模式，但用当前配置创建新轮次；重新选整批目标，不是只重跑原失败item；可能复用同签名活动轮次 |
| 企业PATCH/批量状态 | 乐观锁revision；即使字段值相同，成功请求仍提升revision；旧revision重放409，不能静默重试覆盖 |
| 社媒decision | 关联revision乐观锁；成功递增并追加决策历史，同值也不是无副作用幂等 |
| review decision | 只处理pending；重复处理409；字段accept另检查当前值等于old_value。决定后同企业同字段同值的其他pending自动关闭（状态 `auto_closed`）；accept后不同值的pending把old_value改写为新的当前值，仍待审核 |
| social links/sync | 规范URL实体与关联`INSERT OR IGNORE`；相同证据指纹更新观察时间。可避免重复实体，但不是严格无变化 |
| 社媒cancel | 存在轮次重复调用可返回ok；只取消queued/running轮次及pending/running项 |
| 人工企业、计划、备份、outreach | 每次产生新记录/文件，没有请求级幂等保护 |
| settings/connections/toggle | 无revision；写指定值，后写覆盖；connections为字段合并 |

普通轮次控制：

| action | 允许原状态 | 目标状态 |
|---|---|---|
| pause | queued/running | paused |
| resume | paused/blocked/partial/failed | queued；仅重置可续项；只有耗尽项时409 |
| cancel | queued/running/paused/blocked | cancelled |

普通 run 终态含 completed/partial/blocked/failed/cancelled；work item 含 pending/running/succeeded/partial/blocked/failed。社媒 run 使用 queued/running/paused/completed/partial/cancelled；item 另有 no_results/skipped。市场关联社媒的登录/验证、会话占用、可重试网络/限流经平台门控保留 pending；手动独立社媒沿用原 blocked 规则，原原因保留 result。平台登录状态不是本地 API 鉴权 HTTP 401。

企业/社媒人工归属、来源读取、业务适配与联系记录状态是不同维度。当前outreach只检查企业拒绝联系与关联confirmed；不检查岗位证据、发送通道、当前内容重复、入口级拒绝或已读回执。

## 8. 导出协议

源码：`api.py:482–493`、`export.py:17–81`。

GET `/api/exports`接收与分页接口相同的q/status/type/market_id/run_id/segment/contact/sort，加format；不接收page/page_size，不受UI每页限制。仅run可推导market。返回：

- CSV：`text/csv; charset=utf-8`，UTF-8 BOM；`Content-Disposition: attachment; filename="leads.csv"`。
- XLSX：`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`；文件名`leads.xlsx`。

CSV企业列：id/name/address/phone/website/city/country/business_types/status/source_kind/notes/created_at/last_checked_at/source_url/observed_at。市场导出加market_segment/market_changed/first_seen/last_seen。**没有联系人邮箱列**。

XLSX有Companies、Contacts、Locations、Sources四表。Contacts列为company_id/name/role/email/url/kind/role_status/association_status/review_status/source_url/evidence/observed_at；来源证据也进入Sources。排除联系人pending/reject/invalid/superseded及adoption_status=excluded。

所有格式排除do_not_contact企业，所以文件企业数可小于列表total。单元格前导`= + - @`等增加单引号保护；不要将导出字符串视为原始无转义数据。未导出完整社媒归属、社媒人工跟进和入口级投递状态；不是可直接发送的统一名单。

## 9. 错误与恢复

| HTTP状态 | 当前来源与响应 |
|---|---|
| 400 | 业务ValueError：`{"detail":"原因"}`，如市场与轮次不符、无官网、confirmed+unknown、未确认归属记账、无效计划/备份 |
| 401 | 缺失/错误本地token：`{"detail":"缺少本地访问令牌"}` |
| 403 | Host/Origin/Sec-Fetch-Site边界拒绝；detail分别为仅允许本机访问/拒绝跨来源请求/拒绝跨站请求 |
| 404 | KeyError统一`{"detail":"记录不存在"}`；未知GET `/api/...`兜底为`{"detail":"接口不存在"}` |
| 409 | Conflict：revision冲突、状态不允许、策略耗尽无法继续、已审核、浏览器/恢复锁冲突、活动任务阻止恢复。恢复：Worker 仅在执行作业时持有 `worker.lock`、空闲时释放，恢复接口最多等待 5 秒拿锁，仍在处理作业时返回“后台正在处理作业，请等待当前作业完成或暂停相关任务后再恢复备份”；queued/running 普通或社媒轮次仍阻止恢复，paused 不阻止 |
| 413 | Content-Length检查失败：`{"detail":"请求过大"}` |
| 422 | FastAPI/Pydantic输入验证；`detail`为错误数组，每项只含 `loc/msg/type`，`msg` 为中文（网址校验、必填、长度、多余字段、非法 JSON 等均已翻译），不再含 input/ctx；不要按400字符串结构解析 |
| 405 | 路由框架的方法不允许响应；没有业务层统一封装 |
| 500 | 未捕获异常；没有统一业务错误码或稳定JSON承诺，SQLite错误不全部转换成400/409 |
| 503 | 前端未构建兜底文本；不是所有后端离线的统一状态 |

例如非法分页的稳定形状是`{"detail":[{"loc":["query","page_size"],"type":"less_than_equal","msg":"…"}]}`；省略号仅表示本文未固定框架英文文案，实际JSON可能还有input/ctx。客户端应按HTTP码和结构处理，保留可读detail，不将HTTP200的blocked/no_results当网络失败。

恢复流程：POST restore取得worker操作文件锁，拒绝普通queued/running和社媒queued/running；校验备份文件名、非符号链接、SQLite integrity_check、user_version∈{1,2}及部分必需表。先备份当前库，再用SQLite backup复制；v1执行market迁移，随后audit/social及 `_migrate_market_social` 迁移，再恢复中断项。后者为旧备份补建三个市场社媒表；缺设置仍默认关闭，不自动回放。并未校验每个JSON值或所有业务逻辑关联。应用API锁只保护本实例，worker还使用文件锁；不要绕过接口直接替换真实库。

## 10. 本次校验与再生成

本次仅对临时目录实例化Store，使用进程内ASGI请求验证200/400/401/403/404/409/422、revision更新、候选归属后人工draft及社媒排队结构；未运行worker。生成时禁止socket connect，未读取真实数据库。schema另在内存SQLite重放并通过integrity_check/foreign_key_check。

原生OpenAPI最小再生成方式（在仓库根目录，仅临时Store，无服务启动）：

```bash
cd <项目目录>
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import json
import tempfile
from pathlib import Path
from leadworkbench.api import create_app

with tempfile.TemporaryDirectory(prefix="lead-openapi-") as directory:
    app = create_app(directory)  # 必须显式传入临时目录，不能省略
    output = Path("docs/technical/openapi.current.json")
    output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
PY
```

不需要启动lifespan或TestClient上下文；不能用默认`create_app()`生成附件，因为默认会实例化真实数据目录。后续互审重点为响应投影、状态边界与附件和源码的一致性，不把本次临时验证称为真实来源或完整回归验收。

## 2026-09-24：官网续扫更新

20为单批候选链接预算。批次尚有队列时run保持活动，不需要用户resume；GET run的progress分别显示企业任务和累计网页数量。POST resume对有checkpoint的官网项保留成功页，只重试失败/未完成队列；旧项无checkpoint时第一次从头扫描。未新增HTTP端点，未向客户端开放checkpoint写入。详见[官网分批方案](website-batch-design.md)。

2026-09-24过滤补充：官网工作项内部summary增加files_unparsed，完成后的run.message包含未解析PDF数量；这些文件不会被错误计为联系人/HTML联系表单，也不伪装为已读取。HTTP协议没有新增端点。

### 2026-09-24：市场复核列表与详情

- `GET /api/tasks/{id}/activity?page=1&status=&run_id=`：返回该市场自动生成的官网复核工作项，以及当前市场成员的单独复核。`source=market/standalone` 明确来源，后者不伪造父任务关系。返回 `items,total,page,page_size,rounds`，每页50项，按轮次创建时间倒序。`rounds` 是该市场的检索轮次。
- `GET /api/refresh-activity`：同样分页，仅返回企业单独发起的复核；含未归属市场的企业。
- `GET /api/runs/{id}/items/{item_id}`：严格匹配运行编号和复核项编号，返回企业、摘要、已有逐页日志、警告、未解析文件及所属轮次。不是该轮次的工作项返回404。不会返回队列和整个抓取检查点。
- 列表状态筛选：空、pending、running、succeeded、partial、failed、blocked。页码必须大于0，非法状态422。所有接口沿用本地令牌鉴权与来源检查。
- `work_items` 尚无单项时间戳，列表显示并明确标注“轮次开始时间”，不把它冒充企业完成时间。旧任务无逐页日志时显示“历史任务未保存逐页日志”。


## 美国地区选择扩展（2026-09-24）

`GET /api/regions` 新增可选 `kind=state|county|place`、`county_id`，返回数组。`POST /api/tasks` 新增可选 `state_id/county_id/place_id/county`；名称与编码或州县关联不一致返回400，输入类型校验仍按422。任务响应增加可选county/geography；县选择不裁剪跨县城市。完整协议见 [美国地区目录设计](us-geography-design.md)。

### 自动任务命名与创建人预留（2026-09-24）

`POST /api/tasks` 不再要求 `name`；旧客户端传入的 `name` 仅兼容接收，不参与新任务命名、检索或去重。后台按“城市 / 州 / 国家 · 企业类型 · 补充关键词”生成名称，关键词统一大小写、去重排序；名称最多展示前两个关键词，长关键词缩略，完整条件保留在 keywords 中。县仅用于筛选城市，不把县误写为城市检索边界。相同条件复用原任务，既有名称不批量重写。规划规则（2026-10-15）：填写关键词时只按关键词生成 Maps 查询，企业类型不追加默认查询；不填关键词时按企业类型使用服装行业默认检索词。

任务响应增加 `created_by_user_id: string | null`。当前为 null；未来由已认证的后台上下文填写，不接受请求体指定创建人。

### 动态国家地区配置

新增 `GET /api/region-configs`，返回支持国家的菜单层级、中文标签和末级分支规则。`GET /api/regions` 新增南非目录。字段与校验见 [动态国家地区设计](dynamic-country-regions.md)。`Task.geography.scope` 新增 `whole_municipality`，末级目标为自治市，不能把旧兼容字段 city 理解为建制城市。

### 地图访问保护

GET /api/overview 新增 maps_policy；POST /api/maps/resume 显式恢复全局地图访问，冷却未结束返回409。见 [地图访问策略](maps-pacing.md)。

### 任务详情流程进度

GET /api/tasks/{id}/activity 新增只读 workflow 投影，详见 [任务流程与进度](task-workflow-progress.md)。

2026-09-26：`workflow.stages[key=refresh].website_summary` 新增按企业统计的官网读取结果和主要原因，包含已处理范围、读到/未读到网页的数量及原因分组。只影响查询展示，不更改工作项状态或恢复策略；字段与历史兼容口径见同一进度文档。

2026-09-25：地图恢复策略保持现有端点；`overview.maps_policy` 增加 phase、next_attempt_at、retries_completed、max_auto_retries；任务 workflow 增加 maps_recovery、maps_queries、maps_query_issue_count，以及地图阶段的 retry_pending/unattempted。返回语义及持久化方式见 [地图低速与恢复策略](maps-pacing.md)。
2026-10-15：workflow 的 `maps_queries[]` 增加 `stop_reason` 与 `failure_kind`（`scraper_error|no_results|restricted|null`），`maps_queries[].history[]` 增加 `failure_kind`；`maps_recovery.phase=query_wait` 的文案按最早待重试项区分“采集程序异常，等待自动重试”与“等待空结果复核时间”；市场 `coverage` 增加 `query_basis`（`keywords|company_types`）与 `query_terms`（≤30）；旧轮次中与关键词无关的类型默认查询执行时直接结束，`stop_reason=skipped_legacy_query`，不访问 Maps、不计入异常记录。

## 2026-09-25：市场社媒集成协议

三个新增路由已在 `api.py` 中声明；OpenAPI 从显式临时目录的应用对象离线生成，不访问运行服务。完整业务规则见[市场社媒集成](market-social-integration.md)。

`PATCH /api/tasks/{id}/social-settings` 接受完整三字段设置，也允许省略字段沿用该 Task 原值；返回更新后的 Task。请求只存未来设置，不改 `runs.snapshot`、关联社媒 `settings.market_options` 或已有工作项，不自动补采历史。

```json
{"auto_social":true,"social_platforms":["facebook","instagram","x"],"social_read_posts":false}
```

`POST /api/tasks/{id}/social` 使用当前显式平台/帖子选择，返回市场父 Run，前端据此切换轮次。活动轮次包括 queued/running/paused，paused 不会被自动继续。无活动轮次则创建 `kind="social"` 且 `snapshot.social_only=true`；历史轮次保持。已有关联运行配置不同或市场没有可处理成员时返回可读业务错误，不静默覆盖快照。

```json
{"social_platforms":["facebook","x"],"social_read_posts":false}
```

`POST /api/social/platforms/{platform}/resume` **无 body**，成功示例为 `{"platform":"facebook","state":"pending","message":"已保留成功结果，将继续待执行任务"}`。future next_attempt_at 未到期返回 409，非法来源枚举返回 422；不会打开登录窗口、改未来市场设置或发送消息。成功时重载已保存的连接配置，更新活动市场父轮次下未取消社媒子运行及仍在排队/运行的手动任务的 `settings.connections`，保留 `market_options` 与既有队列范围；不改已结束/取消的手动报告，不解除其他来源门控。执行是否继续仍取决于父轮次、后台与来源队列。已结束手动运行需另行创建重试运行。市场自动搜索设置依然仅接受 Facebook、Instagram、X，恢复接口的可读来源枚举不扩大市场搜索范围。

`workflow.social_only` 区分仅社媒轮次；`workflow.stages` 追加 `social`，标准计数为企业；`workflow.social.run_id` 指向完整报告 `/api/social/runs/{child_id}`。平台企业计数与报告处理项计数不能混用，字段、等待状态和上游未关闭的终态规则见[任务流程与进度](task-workflow-progress.md)。评审关闭记录、测试数量和部署状态见[实施审计](../validation/2026-09-25-market-social-integration.md)。

关联 SocialRun 的 `parent_run_id/task_id` 用于从企业社媒报告返回所属市场；前端将其暂停/继续/取消操作交给父轮次，独立运行保留自身操作。市场子运行不禁用企业内独立搜索/补资料按钮，`POST /api/social/runs` 的活动去重也不复用市场子运行。令牌 `claim_token` 只在内部数据中使用，API 不返回。当前 API 未声明业务 response_model，因此这些响应扩展由本文与实现共同定义，不能仅凭 OpenAPI 空成功响应识别。

Run.progress 增加 `social_total/social_done/social_accounts`；仅社媒父轮次的通用 total/done 同时使用社媒企业数。Task.latest_run 为 social 时，Task.coverage 和 market_stats 仍保留最近 discovery 的发现口径，不用社媒轮次的空地图工作项覆盖。部分有效预览可在 pending 重试期间保留；最终聚合同时检查项 state 与 result.partial，部分成功不冒充全部成功。

滚动/帖子读取中迟到的登录、验证、HTTP 或网络异常保留结构化 reason，已有预览与帖子保留；市场平台仍进入相应登录等待或冷却。账号在 profile/read 期间被标为“不采用”时，在途来源协作取消，结果提交再次检查关联状态；迟到结果不新增观察、帖子或后续工作。以上修复不新增端点、请求字段或数据库结构。

## 2026-10-18：P0 修复

本节同步正文第 3–9 节在 P0 修复后的变化；与上文冲突时以本节为准。

### 企业分页与列表（第 4、5 节）

- 新参数：`category`（可重复，≤20 个，每个 1–120 字符，OR，按 NFKC+casefold+压缩空白后的 `category_key` 匹配）、`excluded=hide|only|include`（默认 hide）、`best_tier`（`named_procurement/named_role/named_unknown/department_email/general_email/form_phone/company_phone/none`）、`sort=tier`。非法值 422。
- `type` 也命中“待核实”类型提示：企业 `business_types` 为空时，按所属市场的 `company_types`（wholesale→wholesaler 等）匹配。
- 企业对象新增：`categories: string[]`（last_seen 降序）、`type_hints: [{business_type,market_id,market_name}]`（仅在 `business_types` 为空时返回）、`exclusion: {rule_id,kind,value,scope}|null`、`best_tier/best_tier_label/best_tier_rank`、`gap: {code,reason,next_step}|null`。`contact_count` 为去重后的联系入口数。分页顶层新增 `excluded_total`（其他筛选不变时被规则隐藏的家数）。
- 列表为两段查询：先按纯 SQL 条件取一页 id，再对这一页批量补充计数、等级、类别、提示、排除命中、营业状态和缺口原因。

### 联系人与详情

- 联系人新增 `tier/tier_label`；人工联系人 `origin:"manual"`、`phone`，字段来源标签“人工录入”。
- 详情 `company` 同样带 `categories/type_hints/best_tier*/gap`。

### 排除规则（`ExclusionRuleInput`，extra=forbid）

| 字段 / 条件 | 规则 | 失败 |
|---|---|---|
| `scope` | global / market | 422 |
| `market_id` | market 必填且存在；global 不得提供 | 不存在 404“市场不存在”；其余 422 |
| `kind` | category / keyword | 422 |
| `value` | 去首尾空白后 1–120 字符，无控制字符 | 422 |
| 关键词 | 规范化后 ≥2 字符 | 400“排除关键词至少 2 个字符” |
| 总数 | ≤200 条 | 409“排除规则已达上限 200 条，请先删除不用的规则” |

作用范围：按市场查看时用全局+本市场规则；不按市场查看时用全局+企业所属任一市场的规则。命中企业在列表、total、导出、类别计数、`tier_stats`、市场统计中默认隐藏；不删数据、不改状态，删规则即恢复。多条命中时类别规则优先，同类取最早创建的规则。关键词按包含匹配（企业名或任一类别）。

### 人工新增联系人（`ManualContactInput`，extra=forbid）

`revision` 必填且须等于企业当前修订号（否则 409“资料已更新，请刷新后重试”）；`name/role` ≤120；`email` ≤254 且格式有效（422“邮箱格式无效”）；`phone` ≤40，仅 `+ 数字 空格 ( ) - .`，数字 7–15 位（422“电话格式无效”）；`url` 公网 http(s)；`note` ≤1000；email/phone/url 至少一项（422）。
`procurement=true` 时 `kind=procurement`，有姓名或岗位 `role_status=explicit`，否则 `department`；不勾选时 `kind=general`（岗位满足采购岗位规则时为 procurement），有岗位 explicit，否则 unknown。成功后 revision+1，来源“人工录入”，审核状态 accept、采用 accepted；之后自动采集到同一邮箱只记版本，不覆盖。其他 409：拒绝联系企业、同企业已有同邮箱联系人、同指纹（同名同电话/同 URL）。

### 审核（第 7 节）

- 联系人变化（同一邮箱再次采到不同姓名/岗位）改为“提议”：联系人行不变，新值存入 `contact_change_proposals`；待审核期间原联系人照常计数、导出。`reviews()` 返回 `contact_change: proposal|legacy|new`，`contact` 为“当前行+提议”。
- accept：只比较 5 个身份键（name/role/kind/role_status/association_status）；当前值已变或联系人已归档时，提议与审核同时作废（superseded，已提交），再返回 409“当前值已变化，本条建议已失效，请查看最新联系人”。非冲突采集改写资料时同步 re-base 待审核提议的 `old_value`。
- reject：只丢弃提议，联系人行与采用状态不变，返回 `kept_previous:true`；同一新值再次出现不再生成审核。已被用户排除的联系人再遇冲突值不生成提议。
- 旧版待审核冲突（行已被旧代码覆盖、无提议行）在用户决定时转换：reject 恢复提议前快照，accept 保留新值，均写 `origin=legacy` 提议行。

### 市场（任务）与轮次

- `task()/tasks()` 新增 `tier_stats`（八个等级的家数，市场成员、未合并、按排除规则 hide）和 `market_stats.excluded`。
- `coverage` 新增 `incomplete`、`incomplete_reason`（`geocode_failed` / `catalogue_no_bounds`）、`geometry_source`（geocoder / catalogue / null）、`geocoder_retry_at`、`relocatable`、`relocate_block_reason`、`run_id`。凡 text_only 计划都视为不完整，`unresolved` 恰有一条以“结果不完整：”开头的原因（历史轮次在读取时补齐），轮次结束为 partial；planning 工作项仍为 succeeded，`retryable` 不因此为 true。
- 规划顺序：place_id 目录范围 → Nominatim → 目录唯一同名地点的范围（“已改用内置城市范围（{vintage}）”）→ text_only。南非自治市（whole_municipality）目录无范围时不调用地理编码。
- `POST /api/runs/{id}/relocate` 矩阵：不存在 404；非市场轮次 400“仅市场检索轮次支持补查”；不是该市场最新一轮检索 409“只能对该市场最新一轮补查”；同市场另有 queued/running/paused 的检索轮次 409“该市场有进行中的轮次，请先等待或暂停”；计划不是 text_only 409“本轮已按城市范围检索，无需补查”；queued/running 409“轮次正在执行，请等待结束或先暂停”；paused 409“请先点继续，或取消后再补查”；cancelled 409“已取消的轮次不能补查，请开始新一轮更新”；completed/partial/failed/blocked 成功：去掉“结果不完整：”原因，planning 置 pending（`{"relocate":true}`），轮次 queued、`finished_at=null`、消息“重试城市定位并补查”。社媒轮次不参与比较。重新规划只忽略 15 分钟失败缓存，不忽略 429/503 冷却与 15 秒间隔；不调用 Maps 访问保护的 resume。
- 官网表单入口：只有联系 / 询价 / 报价 / 供应商合作 / 经销申请类页面（按 URL 最后一段与页面标题判断；blog、news、privacy、terms、careers、products 等栏目下的页面一律不算）上的 `<form>` 计为入口，每个企业官网一次扫描最多 1 个表单入口；没有联系类页面带表单时，扫描完整走完时（队列为空、没有失败页面、站点未暂停）保留最早发现的 1 个其他页面表单；部分完成的扫描先把候选留在 checkpoint，“继续”后重试的联系页若有表单则优先。已入库的旧表单入口不删除，下次扫描按新规则生成。“Contact us”等链接入口不受影响。
- 官网复核工作项 `summary.entries_added` 为去重后新增联系入口数；结束消息为“本次官网扫描读取 N 页，新增联系入口 M 个（去重后）”。

### 导出（第 8 节）

- `layout=companies`（默认）：列与表名不变；XLSX 的 Contacts/Locations/Sources 末尾追加 `company_name`。
- `layout=entries`：CSV 仅“联系入口”；XLSX 为“联系入口”“企业”“地点”“来源证据”，每张表第一列“企业名称”。联系入口列：企业名称、企业编号、实际城市、城市依据、目标市场、来源类别、业务类型、客户匹配状态、入口等级、渠道、联系方式、其他联系方式、联系人、岗位、来源、来源链接、采集时间、审核状态、企业官网。一行一个入口（与详情相同的分组：同邮箱合并、来源合并）；企业电话另起一行“仅企业电话”。“目标市场”按市场筛选时为该市场名，否则为所属全部市场（按 first_seen，用“、”连接）。实际城市从地址解析（US 州+邮编前一段，ZA 4 位邮编前一段），失败时用目标城市并注明“目标市场（地址未解析）”。
- 两种布局都排除拒绝联系企业、不可用/不采用联系人、非客户开发用途联系人和按规则隐藏的企业。导出仍为同步请求。
- 公式注入保护：以 `=`、`+`、`-`、`@`、制表符或回车开头的值，CSV 前加 `'`（含电话）；XLSX 不加撇号，这些单元格显式写成文本类型（`data_type='s'`，数字格式 `@`），Excel 打开时原样显示、不会作为公式执行。

### 新增错误文案（第 9 节）

见上文各表；另有 400“仅市场检索轮次支持补查”、400“无效导出布局”（直接调用 Store 时）。
