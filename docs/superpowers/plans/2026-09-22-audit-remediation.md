# 评估问题修复计划

依据 docs/audits/2026-09-22/产品体验与测试评估.md，用户已授权实施全部问题。

1. F1–F4：地点来源别名、人工采用保护、联系人独立采用决定、同邮箱姓名归属版本。保留原始证据和历史。
2. 修复营销文案误作人员/岗位，增加保守字段验证与可逆旧资料隔离。
3. F5–F7/U3：覆盖能力边界、暂停计划不消耗周期、状态历史、worker 心跳与队列区分。
4. F8/F9/U1/U2/U4/U5：地区搜索、导出一致、业务状态编辑、采购优先、门店折叠、URL 恢复、联系能力筛选排序及批量标记。
5. 隔离数据库跨流程测试、完整回归、构建和浏览器验收；真实数据库一致性备份后加载新版，保留业务决定和任务进度。

分工：主线程 db.py/api.py/worker.py；架构代理 market_store/market_worker/market_plan/maps/export；识别代理 website/contact_fields/quality；界面代理 frontend。测试文件按职责隔离。

接口：contact=email|procurement；sort=newest|procurement|email|updated。contact_decisions 存采用状态；详情返回 adoption_status。批量状态全事务、修订冲突不部分写入。覆盖策略穷尽明确说明，不能假报完整。
