# 后端 PRD 验收矩阵

日期：2026-09-24。范围：`docs/product/*.md`（5份）与 `docs/technical/*.md`（11份）逐份分段阅读全文；以新版 PRD-01～11及当前源码为基线。UI按钮位置、响应式、路由草稿与布局由父任务验收，本报告不声称完成视觉验收。

**本轮结果：549 passed，44 subtests passed，0 failed，0 skipped；2条依赖弃用警告。** 全量执行起始时间为2026-09-24 12:30:38 +08:00，耗时28.05秒。新增专项文件31项均通过；另外518项为已有测试。本轮修复1个后端缺陷；另复现2个非法备份场景返回500，交父任务处理。测试通过不代表完整获客闭环通过；PRD-09仍未实现。

## 证据与安全边界

- 完整逐项结果（原始证据已归档，不随交付）。
- 机器结果（JUnit XML）（原始证据已归档，不随交付）。JUnit有593个case元素，包含44个subtest结果；按classname/name去重为549个pytest测试，不能把593另称549之外的独立业务场景。
- 专项＋门店回归：[backend-focused.log](backend-focused.log)，35 passed（31新增＋4已有）。
- 初次补测：[backend-focused-initial.log](backend-focused-initial.log)，26 passed、5 failed：1项真实市场归属缺陷，1项测试夹具误把非空kind变化当空值补全，3项测试错误预期业务校验为422。后四项修正夹具/契约断言，不是后端修复。
- 安全启动器：[run_backend_offline.py](run_backend_offline.py)。只允许临时目录Store/SQLite；网络只允许本测试进程绑定的回环端口；真实浏览器/Maps二进制禁止启动。已有Maps测试的临时假可执行文件、受控Python清理子进程及本地假HTTP服务仍正常运行。
- 未实例化生产Store，未读取生产业务库、浏览器profile或真实凭据；未启动服务、执行真实爬取或发送。测试中的密钥均为原有测试自造字符串，目录为pytest临时目录。

复现命令（项目根目录）：

```bash
.venv/bin/python docs/validation/2026-09-24-prd-ui/run_backend_offline.py tests -v --tb=short --junitxml=docs/validation/2026-09-24-prd-ui/backend-junit.xml > docs/validation/2026-09-24-prd-ui/backend-full-suite.log 2>&1
.venv/bin/python docs/validation/2026-09-24-prd-ui/backend_restricted_probes.py
```

状态口径：`tested`为所列实现范围在本轮离线测试通过；`partial`为只有部分需求已实现/验证；`unimplemented`为目标能力尚无完整实现；`external unverified`为真实来源、真实身份准确率或外部效果未验证。父任务拥有的UI验收项在此为partial，不据源码推断实机通过。

下文`A::测试名`指 [`tests/test_prd_acceptance_20260924.py`](../../../tests/test_prd_acceptance_20260924.py)；其余测试路径均相对项目根目录，每项实际执行结果可在全量日志搜索。

## PRD逐条验收

| 需求／验收子项 | 状态 | 本轮证据与能力边界 |
|---|---|---|
| PRD-01：市场关键词、企业类型保存、规范化和重复定义复用 | tested | `test_api_worker.py::test_market_api_ignores_old_quantity_limit_and_reuses_same_market`；`test_market_store.py::test_market_identity_planning_jobs_and_resume`；`test_market_plan.py::test_keywords_types_stable_dedup`。市场limit固定0，不由用户填写企业总上限。 |
| PRD-01：销售产品、目标岗位、排除条件、联系语言、素材配置；未知不猜成交潜力 | partial | `tasks`只有地域/关键词/企业类型等。没有完整产品/ICP/岗位配置对象；未知条件的UI展示由父任务核对。不能宣布PRD-01整体通过。 |
| PRD-02：来源可追溯，计划扩展、保存进度，饱和与失败可区分 | tested | `test_market_worker.py`5项；`test_market_plan.py`31项；`test_maps.py`34项。包含空网格不阻断其他网格、饱和细分、不按120条截断整个市场、持久化查询来源。 |
| PRD-02：能力耗尽不承诺继续扩大覆盖 | tested | `test_audit_market_backend.py::test_f5_exhaustion_is_durable_and_cannot_call_source_on_replay`、`test_f5_resume_contract_preserves_exhausted_leaves_but_retries_other_work`；`test_market_store.py::test_unresolved_coverage_prevents_completed`。耗尽叶子保留，resume只处理可续项。 |
| PRD-02：目标城市与企业实际地域分开；范围外/无法定位处理 | partial | 范围定位失败有bbox空值、警告与文字查询降级测试；`ingest_market`新企业city/country仍可来自任务。严格行政区判定、范围外独立筛选尚未形成闭环。 |
| PRD-02：换国家/州不带旧城市；真实全城召回 | partial / external unverified | 输入联动属于父任务；本轮无真实城市样本及总企业数分母，不报告100%覆盖。 |
| PRD-03：同来源重复运行不产生重复企业，首次进入市场才计new | tested | `A::test_prd03_replay_empty_fill_manual_conflict_and_market_membership`；`test_market_store.py::test_two_runs_membership_changed_not_seen_and_manual_preservation`、`test_cross_market_new_is_local_and_strong_identity_keeps_branches`。本轮修复旧观察跨市场漏记归属。 |
| PRD-03：同品牌门店独立、同店搬迁保留历史、迟到旧值不回滚 | tested | `test_branch_lifecycle.py`4项；`test_audit_market_backend.py::test_f1_place_alias_move_keeps_one_location_and_contacts`、`test_f1_conflicting_known_cids_do_not_merge_branches`、`test_f1_delayed_observation_can_teach_alias_without_reverting_move`。 |
| PRD-03：new/existing互斥，changed独立，未再次找到不等于停业 | tested | `test_market_store.py::test_two_runs_membership_changed_not_seen_and_manual_preservation`、`test_existing_empty_phone_fill_is_changed_but_initial_new_company_fill_is_not`；既有市场统计与显式营业状态观察。 |
| PRD-03：复杂主体合并、撤销、全部业务修改可追溯 | partial | 自动/人工字段变化、联系人版本、来源别名和采用决定已有测试；无通用合并撤销接口，不能从已测场景推论所有真实历史数据完整。 |
| PRD-04：有官网/无官网均可发现社媒候选 | tested | `test_social_platform_search.py::test_platform_search_without_website_or_accounts`、`test_worker_search_persists_only_candidates_with_source`；`test_social_discovery.py::test_missing_website_is_skipped_not_no_results`；无官网不是企业无账号。 |
| PRD-04：未尝试、真实无结果、登录需要、不可读、失败区分 | tested | `A::test_prd04_source_status_retains_reason_and_does_not_confirm_identity`6参数；`test_social_discovery.py::test_search_partial_failures_are_not_empty_success`；保留原始status/message，blocked不写成成功，尚未读取时pages为空。 |
| PRD-04：平台与发现方式分别存储，三平台实际可用 | partial / external unverified | `social_entities.platform`与`social_evidence.method/source_url`分开，来源和候选保留测试通过。电话/域名/城市多线索完整匹配链路及FB/IG/X真实登录和抓取未验证。 |
| PRD-05：采购姓名、岗位、邮箱需各自证据，栏目/标题不造人 | tested | `test_contact_quality.py`9项，`test_extractor_quality.py`27项，`test_contact_fields.py`5项；同块证据、第三方/占位邮箱、部门共享邮箱不继承旁边人员字段。 |
| PRD-05：确认账号归属不等于确认采购员工 | tested | `A::test_prd04_source_status_retains_reason_and_does_not_confirm_identity`；`test_social_profile.py::test_matching_facts_do_not_confirm_ownership`、`test_x_profile_does_not_extract_contacts_from_posts`。社媒结果不自动建contacts。 |
| PRD-05：关键员工定义、具名人员—企业—岗位—多渠道统一确认 | partial | contacts与company_social仍分离；scope=employee不关联具体人员。真实岗位识别正确率为external unverified。 |
| PRD-06：采购优先、其他邮箱不重复计采购邮箱、排除招聘等无关入口 | tested | `test_outreach_categories.py`2项；`test_contact_projection.py`7项；`test_audit_market_backend.py::test_u5_usable_contact_filter_counts_sort_and_export`、`test_u5_unusable_contact_states_never_count_filter_or_export`。 |
| PRD-06：同平台账号实体去重、同一已确认入口证据归组 | tested | `test_social.py::test_shared_accounts_decision_protection_and_post_separation`；`test_contact_projection.py::test_same_contact_multiple_sources_one_card_preserves_evidence`。品牌账号共享实体，企业关联独立。 |
| PRD-06：同人跨平台聚合、两人共享邮箱仍各自为人、市场唯一账号计数、待确认单列 | partial | 当前按邮箱合并仍不能满足完整Person/Endpoint模型；social_count包含未排除candidate。不能把采购/邮箱/账号直接相加为人数，也不能把候选计数称全部已采用。 |
| PRD-06：四列表、正文不串位、帖子只在详情 | partial | 本轮后端字段投影/岗位证据测试通过；四列、按钮及帖子位置需父任务DOM和UI结果。 |
| PRD-07：空值补全、格式变化不审核，真实变化与人工保护冲突可处理 | tested | `test_auto_fill.py`3项、`test_format_reviews.py`3项；`A::test_prd07_empty_contact_fill_conflict_and_export_adoption`；`A::test_prd03_replay_empty_fill_manual_conflict_and_market_membership`。无意义重播不新造联系人；实质冲突暂不进入可导出名单。 |
| PRD-07：读取失败/字段缺失保留旧资料及原日期 | tested | `A::test_prd07_failed_website_payload_cannot_replace_successful_data`3参数，成功资料后失败响应即使携带坏联系人也不替换；`test_social_refresh_semantics.py::test_sparse_success_preserves_fields_and_their_actual_dates`、`test_failed_read_does_not_promote_untrusted_fields_or_refresh_old_date`、`test_empty_field_values_do_not_erase_previous_values`。 |
| PRD-07：缓存不伪造观察，已不采用记录不复活 | tested | `test_social_refresh_semantics.py::test_shared_account_cache_does_not_create_a_new_observation`、`test_legacy_cached_snapshots_do_not_change_actual_observation_dates`；`A::test_prd07_empty_contact_fill_conflict_and_export_adoption`。真实样本覆盖度仍external unverified。 |
| PRD-08：已确认社媒目标可保存人工草稿/备注 | tested | `test_social.py::test_api_social_workflow_and_invalid_inputs`；`A::test_prd11_backup_restore_preserves_provenance_and_human_decisions`验证保存的人工草稿和目标关系可恢复。不是完整发送草稿对象。 |
| PRD-08：邮箱与社媒统一准备、主题/正文独立版本、切换对象/页面不丢稿 | partial | 邮件准备接口、统一目标草稿实体未实现；父任务可能修复现有前端社媒内存草稿，不能据此声称邮箱闭环或服务端独立草稿完成。 |
| PRD-09：用户确认实际发送；真实收件；防重；超时未知/回执/退信；最终准入 | unimplemented | `social_detail.capabilities.auto_send=False`；没有sender/outbox/send_command/delivery模块或当前API。规划`outreach-planned.md`不是实现。本轮真实投递测试数为0，邮件/FB/IG/X均未验收，不能计入549个通过项。 |
| PRD-10：人工draft/contacted/replied/closed历史，共享账号既有联系 | tested | `test_social.py::test_outreach_requires_confirmed_association_and_respects_refusal`；`A::test_prd11_backup_restore_preserves_provenance_and_human_decisions`。记录和归属恢复不变；保存记录从不调用发送服务。 |
| PRD-10：企业拒绝后阻止新人工记录、丢弃迟到社媒资料、停用到期复核 | tested | `A::test_prd10_refusal_rejects_late_social_result_and_disables_due_schedule`；`test_social_discovery.py::test_jobs_retry_cancel_and_refusal`。保存过的历史仍可读。 |
| PRD-10：邮件统一历史、真实回复同步、销售结果、入口级拒绝及发送阻断 | partial | 企业级拒绝已有；入口级拒绝/自动回复/真实发送尚无实现。界面“人工记录”标签由父任务核对。现有拒绝守卫通过不等于PRD-09发送守卫验收。 |
| PRD-11：当前筛选范围CSV/XLSX导出，跨表关联，默认排除拒绝联系 | tested | `A::test_prd11_api_export_matches_combined_filter_and_excludes_refusal`2格式，联用run/segment/contact/q/sort及page_size=1；文件仅含被筛选且未拒绝的企业；XLSX四表company_id一致。`test_store.py::test_export_not_truncated_to_ui_limit`覆盖501行；`test_market_store.py::test_1000_companies_database_pagination_and_unlimited_export`覆盖1000企业查询。 |
| PRD-11：排除不采用/待审联系记录、来源时间、导出公式保护 | tested | `A::test_prd07_empty_contact_fill_conflict_and_export_adoption`；`test_audit_market_backend.py::test_u5_unusable_contact_states_never_count_filter_or_export`；`test_api_worker.py::test_manual_entry_is_explicit_and_export_excludes_refusal`覆盖CSV公式前导保护。不是所有电子表格客户端的安全认证。 |
| PRD-11：联系入口级完整导出（含社媒账号和触达历史） | partial | CSV为企业表；XLSX为Companies/Contacts/Locations/Sources，无完整社媒/人工跟进表。不能把账号放空邮箱列冒充完整导出；目标入口格式尚未实现。 |
| PRD-11：数据库备份恢复保留来源、采用决定、人工字段、社媒归属/草稿 | tested | `A::test_prd11_backup_restore_preserves_provenance_and_human_decisions`；恢复前后完整详情一致，integrity_check=ok，foreign_key_check为空。`test_market_store.py::test_v1_migration_backfill_restore_and_v2_backup`验证合成v1升级/恢复。 |
| PRD-11：非法恢复拒绝、运行任务保护、无效文件清晰错误 | partial | `A::test_api_restore_invalid_name_leaves_data_intact`4项、`test_api_restore_symlink_and_active_refresh_are_rejected`、`test_social_connections.py::test_restore_blocks_social_queue`均通过。非SQLite文件/缺必需表返回500，见下方未修复发现。备份仅SQLite，不含profile/凭据/原始文件。 |
| PRD-11：日历计划、停用、错过多期合并、暂停延期、恢复 | tested | `test_store.py::test_month_end_and_missed_occurrences_preserve_calendar_anchor`、`test_schedule_dispatch_is_idempotent_and_coalesces_missed_runs`；`test_audit_remediation.py::test_due_schedule_waits_for_paused_run`；`A::test_api_schedule_invalid_definition_never_creates_schedule`5项；`test_worker_scheduling.py`12项。定时须应用Worker运行与电脑唤醒，真实长期运行/睡眠恢复未专项测量。 |

## 技术协议与2026-09-24增量设计

| 范围 | 状态 | 实际证据 |
|---|---|---|
| API本地边界：Host、Origin、跨站、token、Content-Length | tested | `A::test_api_boundary_rejects_write_before_mutation`6项，均在插入前拒绝，库保持空。不是公网账户认证或完整流式大小限制。 |
| revision/批量原子、参数/未知记录错误 | tested | `test_audit_remediation.py::test_batch_revision_conflict_is_atomic`；`test_store.py::test_revision_prevents_lost_updates`；社媒revision重复409；新计划模型与业务400/422分别断言。 |
| 官网安全边界、robots、重定向、解析/响应预算 | tested | `test_website.py`72项，`test_website_failure_logs.py`16项，来源替身/合成HTML。没有线上安全渗透测试或真实网站读取结论。 |
| 官网20链接为单批，续扫不重读、检查点、同run轮转、失败页重试 | tested | `test_website_batches.py`10项：26页两批、请求预算保留目标、失败页手动重试、取消恢复、跨批robots延迟、同URL解析警告保留。 |
| 商品/媒体/RSS过滤、PDF单列未解析 | tested | `test_website_filter.py`6项；`test_maps_failure_logs.py`13项；`test_website_failure_logs.py`16项。PDF不是已读取联系人。 |
| 市场复核列表/独立复核归属、item与run匹配、响应不暴露checkpoint | tested | `test_run_activity.py`4项，`test_run_company_label.py`1项。本轮未改`run_activity.py`、`api.py`或`db.py`。 |
| 单Worker轮转、暂停/取消、恢复、心跳 | tested | `test_worker_scheduling.py`12项；`test_social_connections.py`临时profile锁。不能推论多Worker高可用、社媒run完全公平或工作项可抢占。 |
| 外部来源及性能 | external unverified | Google Maps/Nominatim/官网/FB/IG/X真实可用性、真实身份准确率、全城召回、性能规模/SLA、真实发送与回复、获客效率/30%节时均未测。 |

## 本轮修复与父任务待办

**B01／已修复：旧来源观察跨市场漏记。** `market_store.py::ingest_market`在timestamp早于listing.last_seen时直接return，未执行`_market_encounter`。同一已知地点首次进入另一市场后，企业总库有记录但目标市场列表为0；本轮new/existing统计也漏记。修复只补市场/run遇见记录及`_outcome/_changed`返回，仍不更新旧地址、电话、地点观察时间或覆盖最新来源值。`_market_encounter`原有max(last_seen)保持时间不倒退。

回归：`A::test_prd03_replay_empty_fill_manual_conflict_and_market_membership`修复前失败（第二市场new=0），修复后通过；既有门店/搬迁4项及全后端549项通过。修改文件仅`leadworkbench/market_store.py`。技术模块文档中“较旧来源提前返回不带_outcome/_changed”的描述因此有小范围漂移，后续文档同步应改为“保留当前资料，仍记录市场遇见并返回分类”。

**B02／待父任务：非法备份返回不可读500。** 两个独立临时文件分别为非SQLite字节、user_version=2但缺少companies等必需表的合法空SQLite；`POST /api/backups/restore`均返回500 `Internal Server Error`，已有企业详情完全不变。期望为可读的无效/不兼容备份错误（400），按IX-09给出下一步。

证据：[backend-restricted-probes.json](backend-restricted-probes.json)，复现：[backend_restricted_probes.py](backend_restricted_probes.py)。根因在`db.py::restore`的PRAGMA/必需表SELECT抛sqlite3.DatabaseError或OperationalError，未转换业务ValueError；API只映射其既有异常类型。本任务不修改受限`db.py/api.py`。这是错误反馈缺陷，不是已确认数据破坏；两次probe不计入549个通过测试。

本轮没有修改前端及三个受限共享文件；不重启服务、不提交代码、不迁移或恢复真实数据。

## 新增测试分布与剩余缺口

| 新增场景组 | pytest参数化后数量 |
|---|---:|
| PRD-03来源重播／空值补全／人工冲突／跨市场遇见 | 1 |
| PRD-07已有资料遇失败保留 | 3 |
| PRD-07联系人空值补全／冲突／不采用导出 | 1 |
| PRD-04来源状态与身份隔离 | 6 |
| PRD-10拒绝、迟到资料、到期计划 | 1 |
| PRD-11多筛选API导出CSV/XLSX | 2 |
| PRD-11恢复来源与人工决定 | 1 |
| API写入前本地边界 | 6 |
| 无效备份名称 | 4 |
| 符号链接及活动复核恢复保护 | 1 |
| 计划跨字段／时区／周期校验 | 5 |
| 合计 | 31 |

原有测试以各模块单点场景为主。本轮补上连续业务步骤下的守卫和数据交付检查，尤其是成功资料之后的失败输入、复核冲突之后导出不可用、复核/归属人工决定经过备份恢复、分页筛选与导出排除规则组合。未实现项不添加“永远跳过即通过”的占位发送测试。

剩余重点：PRD-01完整产品配置；PRD-02真实地域核验；PRD-05/06人员—入口模型与待确认口径；PRD-08邮件/社媒统一目标草稿；PRD-09真实投递及重复/未知/退信状态机；PRD-10回复同步和入口级拒绝；PRD-11完整入口与历史导出。UI与业务输入由父任务汇总，真实平台评估需另行获准开展。
