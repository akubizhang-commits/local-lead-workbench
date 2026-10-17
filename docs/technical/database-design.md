
### 任务创建人扩展（2026-09-24）

新增 `task_metadata(task_id TEXT PRIMARY KEY REFERENCES tasks(id), created_by_user_id TEXT DEFAULT NULL)`。启动时幂等建表，新建市场写入空创建人；历史任务没有扩展行时返回 null。与原 tasks 表分离，兼容已有存储结构。后续登录系统接入时再关联用户表并由服务端写入，当前不虚构创建人。
