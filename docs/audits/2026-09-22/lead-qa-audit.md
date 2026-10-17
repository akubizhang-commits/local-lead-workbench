# local-lead-workbench 产品测试评估

审查日期：2026-09-22。对象：`<项目目录>` 当前工作目录版本。

**结论：基础测试与构建通过，但补充产品验收未通过。确认 5 个可复现缺陷，其中联系人身份审核和拒绝后的采购分组问题应优先处理。可以继续受控内部测试，暂不建议把当前采购联系人列表当作已可靠落实人工审核决定的业务名单。** 本次只提交评估和复现，不修复代码。

## 协同审查收口说明

收到父级最新分工后，本轮不再扩展测试范围。以下为父级/架构 agent 提供的结果，未作为本报告独立复现或新增缺陷：接受字段缺少 overrides 后被 Maps 覆盖、拒绝联系人再次 pending 导致导出回流、place_id 升级 CID 搬迁重复、饱和 leaf resume 无推进、paused 消耗 schedule；以及 worker 无心跳时排队算运行中、related 54 张卡片阻挡采购联系人、业务类型无编辑入口。父级已完成的 55 条 UI 分页、状态编辑筛选、CN 手工地区、任务控制与临时备份不重复投入。

本报告 D2 是“拒绝决定后立即仍计入前端采购分组”，实测该时刻 XLSX Contacts 为 0；不是“新观察触发 pending 后重新导出”的路径。`test_rejected_contact_unchanged_refresh_stays_rejected` 仅证明完全相同 payload 重放仍被排除，不能据此否定架构 agent 的新变化路径。

可立即交付的 API/导出正确性证据：组合筛选与分页 ID 集合一致、CSV/XLSX 全量结果一致、四种市场分类与历史轮次筛选正确、三组企业类型别名一致、拒绝联系及其关联表记录被排除、中文/换行/电话文本与表间 ID 关联通过。已证实的缺口是 D5：仅提供 run_id 时 CSV/XLSX 同时丢失四列市场元数据；D4：地址中存在的地区文本未被检索且同筛选导出漏项。未增加缺陷数，也未开展更多架构或 UI 检查。

## 1. 执行边界与证据可信度

- 所有数据库测试都使用 pytest `tmp_path`。额外脚本只通过 `create_app(tmp_path, allowed_hosts={'testserver'})` 构造 Store；从未实例化 `Store('data')`、默认数据目录的 Store 或无参数 `create_app()`。
- 未读取或修改真实业务数据库；未请求正在运行的 HTTP 服务；未操作用户浏览器，未停止服务。
- HTTP 验证使用 FastAPI `TestClient` 在进程内调用实际路由。官网观察与联系人输入是合成 payload，经实际 `Store.apply_enrichment` 进入业务逻辑；不访问真实网站，不声称验证了真实网页采集准确率。
- 前端采购分组复现通过 Node 直接导入现有 `frontend/src/groupContacts.ts`，输入来自真实测试 API。结合组件源码确认渲染与计数的调用关系；未进行浏览器端点击、视觉或端到端测试。
- 前端构建实际执行项目 `npm run build`（`tsc --noEmit && vite build`），仅将 `outDir` 改到新建临时目录，避免覆盖运行中服务的 `frontend/dist`。
- 没有修改项目源码、项目测试、配置或真实业务数据。额外脚本、报告和日志保存在 `/tmp`，构建产物也在 `/tmp`。测试运行可能产生 Python 常规字节码缓存；pytest 禁用缓存提供器。
- 队列恢复、作业覆盖、企业去重不作为本次专项缺陷范围，留给架构 agent。原有全套测试中的这些用例照常运行。额外市场测试只在临时库构造历史轮次，不执行后台队列。
- 项目当前 `git status` 显示主要文件均未跟踪，没有可引用的已提交版本差异；本报告引用实际读取的文件和行号，不将既有未跟踪文件算作本次改动。

## 2. 检查命令与结果

环境：Python 3.14.3、Node v22.22.2、npm 10.9.7。后端使用项目 `.venv`，前端使用已有依赖，未执行安装或升级。

| 检查 | 实际命令与工作目录 | 结果 |
|---|---|---|
| 全套后端 pytest | 项目根目录：`.venv/bin/python -m pytest -q -p no:cacheprovider` | **188 passed，41 subtests passed，2 warnings，17.81s，退出 0** |
| 全套前端测试 | `frontend`：`npm test` | **7 passed，0 failed，147.37ms，退出 0** |
| 前端类型检查与构建 | `frontend`：`npm run build -- --outDir /tmp/lead-qa-build.EomIRB` | **通过，退出 0**；43 个模块，Vite 阶段 483ms |
| 补充产品测试 | 项目根目录：`.venv/bin/python -m pytest <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py -q -s -p no:cacheprovider` | **57 passed，6 failed，2 warnings，4.08s，退出 1**；63 项参数化用例，6 次失败归并为下文 5 个缺陷 |

补充失败都是明确的产品期望断言，不使用 `xfail` 或跳过来掩盖；不是脚本异常或环境依赖缺失。缺陷 5 的 CSV、XLSX 各失败一次。

后端两条警告是 Starlette/httpx 与 anyio BlockingPortal 的弃用提示，本轮未造成失败。构建提示临时 `outDir` 不在项目根目录、不会自动清空，这是有意使用全新临时目录的结果。

证据文件：

- [原有 pytest 日志](<项目目录>/docs/audits/2026-09-22/lead-qa-pytest.log)
- [npm test 日志](<项目目录>/docs/audits/2026-09-22/lead-qa-npm-test.log)
- [npm build 日志](<项目目录>/docs/audits/2026-09-22/lead-qa-build.log)
- [额外测试完整断言、实际值与回溯](<项目目录>/docs/audits/2026-09-22/lead-qa-extra.log)
- [额外复现脚本](<项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py)

## 3. 专项覆盖与已证实行为

| 范围 | 数据和验证点 | 结果 |
|---|---|---|
| 企业筛选分页 | 临时创建 63 家企业；组合地区文本、verified 状态、wholesale 别名筛选；31 条结果按每页 7 条取 5 页，核对总数、ID 集合、不重不漏；越界页为空 | 通过 |
| 分页参数 | 0/负页码、非数字页码、page_size 0/201、非法 segment | 全部按预期 422 |
| 类型筛选 | wholesale↔wholesaler、retail↔retailer、import↔importer；API 分页及 CSV/XLSX | 通过 |
| 市场/轮次筛选 | new、existing、changed、not_seen 均构造非空结果；显式轮次、默认最新轮次、历史轮次排除后来新增成员 | 通过；changed 标志在临时库植入，此项只验证筛选消费该标志，不评估变化检测算法 |
| 非法市场参数 | 不存在的市场/轮次 404；无市场的 segment 400；不匹配的 market_id/run_id 400 | 通过 |
| 导出分页无关性 | 组合筛选结果全量导出，与多页 ID 集合一致 | 通过；原有测试另覆盖 1000 家 Store 分页，不将其误称为新增的 1000 家 HTTP 导出测试 |
| CSV/XLSX 数据 | 中文、逗号与换行备注、公式前缀、带 `+00` 的电话文本；XLSX 企业/联系人/地点/来源按企业 ID 关联 | 通过；安全前缀使用单引号，文件中的字符串被保留。未以 Excel 应用实际打开验证显示效果 |
| 拒绝联系 | do_not_contact 不导出企业，也不导出其联系人/地点/来源；显式只筛选拒绝联系时导出为空 | 通过 |
| 空字段补全 | 空 phone/address/business_types 自动填充无审核；人工先填写再清空的 phone 可补全；联系人空 role/kind/role_status/association_status 可补全 | 通过 |
| 失败与缺失字段 | failed/blocked 不应用 payload；completed/partial 的空 phone 不清空现有值；partial 联系人可补全但不更新成功检查时间 | 通过 |
| 非空企业字段冲突 | 保留现值、创建审核；接受后更新；拒绝不变；人工再次编辑后旧审核接受返回 409 | 通过 |
| 联系人身份维度 | role、kind、role_status、association_status 各自发生变化后生成新旧值审核；accept/reject 状态正确，重复决定 409 | 接口通过；姓名变化和拒绝后的业务分组见缺陷 1、2 |
| 联系人拒绝持久性 | 相同证据再次刷新仍为 reject，不重开审核，XLSX 不导出 | 通过 |
| 手动状态与并发 | 四种状态能保存、筛选；revision 增加；旧 revision 返回 409；后续补全不清除状态和备注 | 通过；历史缺口见缺陷 3 |

### 手动状态与审核语义

1. `candidate`、`verified`、`not_fit`、`do_not_contact` 是人工业务标记。`verified` 不自动构成营业状态证据；从空库手动标记后 `operating_status` 仍为 `unknown`，`last_checked_at` 仍为空。
2. `last_checked_at` 由成功的官网补全更新。前端将它标为“最近核验”，应理解为成功检查时间，不能据此推断人工何时核实。此处记录语义，不另外计为缺陷。
3. `not_fit` 当前仍参与默认导出；只有 `do_not_contact` 被强制排除。后者的手动 refresh 返回 409。按现有明确规则验收，不擅自把“不符合”理解成“拒绝联系”。
4. 企业字段审核的“拒绝”保留原值；联系人审核按钮明确写“标记为不采用”，实现只写 `review_status=reject`，并不回退原岗位。因此，本报告**没有把联系人拒绝不回退原岗位单独报为缺陷**；缺陷 2 是不采用的身份仍进入采购分组和数量。
5. 联系人冲突 payload 在待审核时已经保存新值并标记 pending，旧值保存在审核内容中；XLSX 仅排除 reject，pending 仍可导出并携带 review_status。现有产品未承诺“仅已接受可导出”，所以不将此行为另报为缺陷。

## 4. 最多五项关键可复现缺陷

优先级：P1＝影响联系人身份判断或执行人工拒绝的正确性，建议业务使用前解决；P2＝稳定复现的追溯、检索或导出一致性问题。以下命令都使用临时库，不应改写成对真实服务发请求。

通用运行入口（任意工作目录均可）：

```bash
<项目目录>/.venv/bin/python <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py
```

只运行单项时在后面加 `-k defect_1` 等。当前版本这些命令预期返回退出码 1，因为脚本断言正确的产品行为。

### D1 · P1：同一邮箱的姓名归属变化绕过身份审核

复现：`.../.venv/bin/python <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py -k defect_1`

操作步骤：

1. 在临时 API `POST /api/companies` 创建 QA 企业。
2. 经补全入口写入 `Alex / alex@qa.example / Sales`，来源 `https://qa.example/team`，有对应证据。
3. 对同一企业再次补全同一来源、同一邮箱，将姓名改为 `Bob`，证据改为 `Bob Sales alex@qa.example`。
4. `GET /api/reviews` 和 `GET /api/companies/{id}` 检查结果。

**Expected：** 已存在邮箱从 Alex 变成 Bob 的人员对应关系应作为身份冲突进入待审核；保留来源事实可以，但不应把冲突的新对应关系无提示自动收录为普通联系对象。这来自需求 FR-07“联系人归属变化时进入待审核”和 FR-05 对人员邮箱对应的要求。

**Actual：** 审核列表为空；出现两条 `auto_recorded` 记录，分别是 `(Alex, alex@qa.example)`、`(Bob, alex@qa.example)`。无法通过待审核页确认或拒绝这次邮箱人员归属变化。

影响：采购/销售邮箱可能换人、来源误配或名字修订时，用户最需要的身份变化审核恰好缺席。这里审查的是联系人身份审核入口，不是架构 agent 的企业去重规则。

源码根因：姓名直接进入 fingerprint，姓名变化找不到 prior，跳过冲突判断，进入自动收录分支。

- [db.py:337](<项目目录>/leadworkbench/db.py:337)：fingerprint 包含姓名。
- [db.py:341](<项目目录>/leadworkbench/db.py:341)：只有同 fingerprint 才检查身份字段冲突。
- [db.py:366](<项目目录>/leadworkbench/db.py:366)：新 fingerprint 固定 `auto_recorded`。
- [可执行复现:212](<项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py:212)。

### D2 · P1：已拒绝的采购身份仍显示并计入“采购联系人”

复现：`.../.venv/bin/python <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py -k defect_2`

操作步骤：

1. 创建企业，补全 Alex，初始 `role=Sales`、`kind=other`、`role_status=explicit`。
2. 再补全 `role=Buyer`、`kind=procurement`，产生联系人审核。
3. `POST /api/reviews/{id}/decision`，请求体 `{"decision":"reject"}`，对应界面“标记为不采用”。
4. 读取详情 contacts，将 API 原始返回值送入产品实际 `groupContacts`；同时读取 XLSX Contacts 表。

**Expected：** 已拒绝的采购身份不应进入可用采购联系人的分组及数量；若需要展示拒绝的来源记录，应以独立的历史/不采用语义展示。

**Actual：** 联系人值为 `Buyer / procurement / reject`；实际分组函数返回 `procurement.length=1`，组件据此显示“采购联系人 1”。同一个人已经被 XLSX 排除，Contacts 行数为 0。卡片虽有“已拒绝”标签，但分组标题和数量仍把它作为采购联系人。

影响：执行了“不采用”的人仍能在采购联系人清单中被当作目标，界面和导出对可采用联系人数量不一致。没有将“拒绝后不恢复 Sales”本身判成缺陷。

源码根因：分组只看 kind 和 role_status，完全不看 review_status。

- [groupContacts.ts:5](<项目目录>/frontend/src/groupContacts.ts:5)：分组条件缺少审核状态。
- [ContactGroups.tsx:8](<项目目录>/frontend/src/ContactGroups.tsx:8)：该分组直接用于标题计数与卡片渲染。
- [db.py:426](<项目目录>/leadworkbench/db.py:426)：拒绝写入 review_status。
- [export.py:52](<项目目录>/leadworkbench/export.py:52)：XLSX 排除 reject，提供交叉验证。
- [可执行复现:221](<项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py:221)。

### D3 · P2：手动核验／拒绝联系状态变化不进入更新历史

复现：`.../.venv/bin/python <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py -k defect_3`

操作步骤：

1. 新建企业，初始 candidate。
2. 逐次获取最新 revision，以 `PATCH /api/companies/{id}` 依次保存 `verified → do_not_contact → candidate`，仅传 status 和 revision。
3. 查询 `GET /api/changes` 及详情中的 changes。

**Expected：** 对影响业务判断和导出准入的状态变化，应可在更新记录里查看旧值、新值和时间；特别是解除拒绝联系，应保留可追溯事件。此项是基于产品提供的资料变化/更新历史能力作出的业务完整性验收要求，并非 API 承诺返回 status 事件的显式协议。

**Actual：** 状态和 revision 正常更新，最终 candidate；全局和详情 changes 都为 `[]`。既不能知道何时核实，也无法从产品历史判断它曾被标记拒绝联系。解除拒绝后重新符合导出规则，但没有相应历史事件。

影响：业务用户无法解释名单重新进入导出的原因；人工状态生命周期无法追溯。当前状态存储正确，本项并非状态无法保存或被采集覆盖。

源码根因：四个资料字段的修改插入 changes，但 status 路径只 UPDATE companies。

- [db.py:308](<项目目录>/leadworkbench/db.py:308)：普通字段记录历史。
- [db.py:317](<项目目录>/leadworkbench/db.py:317)：状态更新未记录事件。
- [可执行复现:234](<项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py:234)。

### D4 · P2：按地区搜索忽略已保存地址中的州／省信息

复现：`.../.venv/bin/python <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py -k defect_4`

操作步骤：

1. 创建名称 QA 企业、city 为 Los Angeles 的企业。
2. 人工地址保存为 `10 QA Street, California 90001`。
3. 在企业库搜索框输入 `California`，对应 `GET /api/company-page?q=California`。
4. 以相同筛选导出 CSV。

**Expected：** 界面提示“搜索企业名称、地区或关键词”；企业明确保存了 California 地址，地区关键词应能命中该企业。至少不能让用户在无单独州省筛选的情况下误以为企业不存在。

**Actual：** 分页 `total=0, items=[]`；CSV 也没有该企业。城市 Los Angeles 的搜索正常，问题限于未纳入搜索列的地区信息。

影响：按州、省、地址中的区域筛选名单时漏项，并同步传递到导出。此项按界面的“地区搜索”产品承诺验收，不声称 API 文档明确承诺全文检索所有列。

源码根因：q 仅对 name、city、website 做 LIKE，地址与 country 未参与。

- [market_store.py:497](<项目目录>/leadworkbench/market_store.py:497)：限定搜索列。
- [App.tsx:27](<项目目录>/frontend/src/App.tsx:27)：搜索框提示及导出沿用筛选。
- [可执行复现:244](<项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py:244)。

### D5 · P2：仅传 run_id 导出时，丢失市场分类与发现时间列

复现：`.../.venv/bin/python <项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py -k defect_5`（CSV/XLSX 两例）。

操作步骤：

1. 在临时库创建市场和轮次，合成一条本轮新增企业。
2. `GET /api/company-page?run_id=R&segment=new`：返回该企业及 `market_segment=new` 等市场信息。
3. `GET /api/exports?format=csv&run_id=R&segment=new`。
4. 与 `GET /api/exports?format=csv&market_id=M&run_id=R&segment=new` 比较表头；XLSX 同样比较。

**Expected：** run_id 已唯一确定市场，查询层也实际解析出了该市场。两组语义等价的筛选应导出一致的列，包括分类、变化标志、首次及最近发现时间。

**Actual：** 两种导出都正确筛中了同一家企业，但仅传 run_id 时只有 15 列；显式加 market_id 时有 19 列。缺少 `market_segment`、`market_changed`、`first_seen`、`last_seen` 四列。分页 API 的相同 run-only 查询却包含它们。

影响：直接按轮次调用 API 导出的用户丢失本轮分类与市场发现时间。当前前端常规市场筛选带 market_id，此问题主要影响 run-only API 路径；不夸大为所有市场导出失效。

源码根因：查询函数内部从 run_id 解析 market_id，但导出表头仍只判断原始 filters 是否显式带 market_id。

- [market_store.py:467](<项目目录>/leadworkbench/market_store.py:467)：从轮次解析市场。
- [market_store.py:481](<项目目录>/leadworkbench/market_store.py:481)：查询已生成市场元数据。
- [export.py:25](<项目目录>/leadworkbench/export.py:25)：表头条件仅看显式 market_id。
- [可执行复现:252](<项目目录>/docs/audits/2026-09-22/test_lead_qa_audit.py:252)。

## 5. 放行意见与限制

- 自动化基础质量门槛通过：现有全部后端和前端测试通过，TypeScript 检查与生产构建成功。
- 本轮产品验收不通过：D1/D2 直接影响联系人身份审核的可靠性；建议至少关闭这两项后，再把采购联系人分组用于实际跟进判断。
- D3 的业务历史要求、D4 的地区检索范围应明确作为产品验收口径；本报告将这些依据与 D1/D2/D5 的具体实现矛盾分别写明，避免将业务期望包装成明确接口协议。
- 没有修代码、改真实数据或回写外部系统，也没有替架构 agent 对队列恢复/覆盖/企业去重下结论。
- 未测试真实网络采集、反爬处理、活体网站身份提取、完整浏览器流程、性能压力、多用户并发或运行中服务健康。通过本次合成测试不等于这些能力已通过验收。
- 建议后续针对五项复现逐一验收；脚本保留正确行为断言，修复后应由失败变为通过。本次没有实施这些修复。
