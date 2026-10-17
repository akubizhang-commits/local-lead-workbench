# 社交账号与联系入口实施计划

Goal：交付已批准方案第一阶段，并提供无需搜索服务凭据的人工发现入口。
Architecture：独立社交实体与企业多对多关联，来源证据和人工决定分别保存；复用官网采集事务、SQLite和本地鉴权API。React企业详情新增账号、已知帖子和跟进。
Tech Stack：现有Python/SQLite/FastAPI、React/TypeScript；不增加依赖。
Spec：社交媒体发现与企业补全方案（原始证据已归档，不随交付）

- [x] tests/test_social.py：分类、反例、去重、共享账号、拒绝保护、历史与鉴权测试。
- [x] leadworkbench/social.py：平台链接规范化，非账号链接分类；查询词模板。
- [x] leadworkbench/social_store.py：迁移、证据导入、人工候选、归属决定、草稿/已联系记录。
- [x] db.py/api.py：启动及官网采集接入；鉴权路由和严格输入。
- [x] frontend/src/SocialPanel.tsx：账号/帖子分组、核对、搜索、跟进；替换原链接列表。
- [x] 测试、构建、隔离API/UI验收；一致性备份后加载本地产品。

约束：不自动发送；不抓登录后内容；未知账号归属不自动确认；帖子不反推企业官方账号；不采用决定不被重复采集恢复。迁移保留原始链接与证据。平台API/全文帖子流/模型采购意图识别留待后续阶段，页面如实说明。

验收与后续边界：docs/validation/2026-09-22-social/第一阶段交付与验收.md
