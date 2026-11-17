# 接口与数据规格

本目录保留源快照的技术契约文件：

- `openapi.json`：v1 的 16 个接口操作、请求/响应、枚举、错误和 revision 字段。
- `template.schema.json`：固定版式模板的画布、字段容量、字体、图片适配及装饰白名单。
- `database.sql`：基础 sessions/assets/tasks/jobs 表结构。应用使用 `backend/migrations/001_init.sql` 初始化基础结构，再通过服务初始化新增账号、素材及工作台扩展表。
- `test_database.py`：基础 SQL 约束检查。

公开快照不携带源项目的内部审批和成员台账。各文件的原始 SHA-256 见根目录 [PUBLICATION.json](../PUBLICATION.json)，不能把源码存在或本地测试通过理解为课程团队已经正式验收。

当前运行应用还提供 `/api/v2/` 扩展，内容见 [API说明](../docs/API.md) 和 [v2 OpenAPI描述](../docs/api-v2.json)。本地启动后也可打开 `/api/docs` 查看实际接口。

运行 `backend/.venv/Scripts/python.exe scripts/check_contracts.py --strict` 可校验 JSON Schema、示例、OpenAPI 及本地文档链接；先按开发说明安装锁定的后端开发依赖。源项目内部审批基线没有随本公开快照复制。
