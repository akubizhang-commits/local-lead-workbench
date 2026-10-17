# 本机端到端验证

日期：2026-09-22。以下为实际程序与浏览器操作结果，合成 fixture 不计入业务库。

## 已验证

- 空库启动、创建洛杉矶查询、启动、失败续跑、五家真实 Maps 企业入库。
- 四个官网字段从原始 web_site 字段修复后重新解析补回，原始来源及时间保留。
- 试点企业A 为独立人工录入官网样本，界面明确保留人工录入来源，未冒充自动发现。
- 对该官网执行公开页面扫描，找到两名 Buyer，正文姓名、岗位和邮箱来自同一人员区块，全部待审核；没有发送邮件或验证收件能力。
- 对 Maps 发现的 Tasha Apparel Wholesale 执行官网复核，实际联系人记录进入企业详情。
- 浏览器创建一致性备份成功。备份恢复、导出、权限检查、人工修改保护、日历月调度与中断恢复通过临时数据库自动化测试；没有为测试恢复而覆盖真实业务库。
- Python 94 项及 41 个 subtests 通过（两条上游弃用提示）；前端 3 项测试、TypeScript 检查和 Vite 构建通过。npm audit 为 0 漏洞。

## 实际运行状态

```json
[
  {
    "id": "741fa660928346db960d9f9b823644b3",
    "kind": "discovery",
    "status": "completed",
    "message": "计划内查询结束，不代表地区全量",
    "error": null
  },
  {
    "id": "85a6b4a44bb447d6a7c42a9a50c0dd93",
    "kind": "refresh",
    "status": "partial",
    "message": "部分任务未完成，请查看原因",
    "error": "https://<试点企业A>/: FetchError: Response body byte limit exceeded；Total website time budget exceeded"
  },
  {
    "id": "06c06daf32204446ba63339f4bf58c06",
    "kind": "refresh",
    "status": "partial",
    "message": "部分任务未完成，请查看原因",
    "error": "https://www.tashaapparel.com/collections/all: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/customer_authentication/redirect?locale=en&region_country=US: Cross-site redirect requires manual identity review；https://www.tashaapparel.com/policies/shipping-policy: robots disallows https://www.tashaapparel.com/policies/shipping-policy；https://www.tashaapparel.com/collections/wholesale-womens-clothing: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-sale-items/women: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-apparel-small-packs: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-basics-clothing: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-clothing: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-outerwear-jackets: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-dresses: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-skirts: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-romper-jumpsuits: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-tops: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-womens-fall-winter-bottoms: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-dresses: FetchError: Response body byte limit exceeded；https://www.tashaapparel.com/collections/wholesale-mini-dresses: FetchError: Response body byte limit exceeded；Page limit reached"
  }
]
```

## 采购联系人证据

- 联系人 A — Buyer — `buyer-a@example.com`；来源 `<试点企业A>/pages/sales-reps`，岗位 `explicit`，关联 `same_block`。
- 联系人 B — Buyer — `buyer-b@example.com`；来源 `<试点企业A>/pages/sales-reps`，岗位 `explicit`，关联 `same_block`。

人员页正文与 mailto 大小写不同的邮箱目前分别保留，因此原始联系入口数量不等于独立联系人数量。跨页面联系人归并、公司合并/撤销、动态官网回退和全球行政边界覆盖属于后续工作，当前不得宣称这些功能完成。

## 本地文件

- SQLite 和备份：项目 data/。
- Maps 查询、日志与原始结果：data/runs/；详见 maps-validation.md。
- 官网字段与联系人证据：SQLite observations、contacts；保存来源链接、原文片段与观察时间，未默认存整站 HTML。
- 调度默认关闭；本次未创建任何真实定时采集计划。计划需应用运行且电脑唤醒。

## 官网扫描策略更新实测

2026-09-22：通过工作台对 Tasha 重跑，run ID `887ef1017e6940ed99f542a58d14a906`。实际读取 20 页，提取 39 条联系记录，按规则跳过 230 个不同无关 URL。联系记录包含不同页面上的相同入口，不等于 39 人或 39 条新增线索。本次仅剩 Page limit reached，未再出现旧运行中的商品页超限、登录跨站跳转和配送政策 robots 错误。旧记录保留，历史与新记录均以中文原因摘要和默认折叠的详细日志展示。

回归验证：Python 105 项与 41 个 subtests 通过（两条既有依赖弃用警告）；前端 5 项测试、TypeScript 与构建通过。


## 2026-09-22 目标市场企业库

- 真实数据库从 v1 迁移到 v2，迁移前一致性备份在 `data/backups/pre-market-20260922T123413.sqlite3`。原有企业和联系人保留。
- 保存用户原表单的 New York服装批发商市场：US / New York / New York City，批发、零售、进口贸易，自动官网补全；未启动此市场。企业数量字段已移除。
- 洛杉矶真实运行 `b88547bda1b046c4bbb977a581397ff2`：两个城市名称关键词检索结束，去重后市场92家，本轮新增87家、原有5家，资料变化2家。未启用本轮官网补全。
- 城市定位接口超时，因此没有执行网格覆盖；状态保留为部分完成。92是搜索候选记录数量，不表示全部符合条件或已覆盖整个城市；不同地址的同名地点保守保留，避免错误合并。
- 修复上游深度搜索在首个15秒统计周期误触发空闲退出的问题，关闭该上游参数，保留适配器180秒超时及暂停取消。
- 浏览器验证市场创建、统计、分页、5家存量筛选、资料变化标签；原有Tasha联系人仍保留。官网补全进度按refresh阶段单独统计。
- 后端179项测试通过，41个子测试通过；独立审查复现5项通过。前端7项测试通过，TypeScript与生产构建通过。

## 2026-09-22 重复企业审核展示修复

- 重复审核用两条企业资料卡展示名称、地址、电话、官网与详情入口，不再渲染内部ID为旧值/新值。
- 重复项仅提供“保留为独立记录”；旧客户端对duplicate_candidate提交accept返回明确错误，不覆盖公司资料。
- 相同官网地址（兼容http/https、www、结尾斜杠）的审核项归组展示，企业详情也展示同官网关联地点。保留路径与查询参数，暂不把不同门店页面推断为同一品牌；所有关联仅用于展示。
- Crossroads Trading两条真实记录已在浏览器核对：47 W 13th St / 24 W 26th St及对应电话清晰分列；未代用户处理真实审核。
- 后端181测试及41子测试通过，前端7测试与生产构建通过。新增测试验证独立保留不覆盖、不同网站路径和空官网不错误归组。
- 已重启本地服务，原运行任务从持久化检查点恢复。

## 2026-09-22 门店免审核与地址更新

- 同名、同网站主机、地址与完整电话号码均不同的记录自动保留独立门店；排除常见共享社媒/目录网站，不据此合并企业。不同门店页面可关联展示。
- 迁移前已建立pre-branch-lifecycle备份；11条旧重复提示自动标记auto_separate，剩余2条继续审核。浏览器确认Crossroads不再待审。联系人审核是另一类，未自动通过。
- 同一Maps地点标识再次命中会更新当前地址、电话及地点坐标，历史观察与变化保留；保留人工字段覆盖保护。更早的返回记录不能把最新地址改回旧址。
- 新增明确营业状态采集与展示：永久停业、暂时停业、明确营业。普通CLOSED/当天打烊不被解释为永久失效。仅状态变化不删除门店，也不覆盖另一条同名记录。
- 未再次命中本身不判断失效；当前不包含对所有未命中旧地点逐个主动重新访问的专项核验，也没有依据不同地点ID自动认定搬迁。状态证据会在后续实际检索命中时记录。
- 185项后端测试及41子测试通过；前端生产构建通过；服务更新后原任务继续执行。

## 2026-09-22 空字段自动补全与联系人审核修复

- 联系人审核解析关联记录为邮箱、姓名、岗位及字段来源，并展示此前岗位等资料；不再把contact ID展示为新值。确认/不采用按钮使用联系人语义。
- 新增联系人直接auto_recorded（自动收录），不代表岗位归属或邮件收信能力已验证；补全已有联系人的空字段、单纯更新证据文本不生成审核，非空身份资料发生变化仍审核。
- 企业空字段自动补全，包括存在人工覆盖标记但当前值为空的字段。旧审核清理时重新检查当前值，不覆盖已经填入的不同值。
- 真实数据库已建立pre-auto-fill备份，147条旧新增联系人审核自动收录，待审核从172降至25；记录未删除。浏览器验证新版列表。
- 后端188测试、41子测试通过，前端构建通过。任务恢复继续运行。
