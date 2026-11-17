# API 使用说明

启动后访问 `http://127.0.0.1:8000/api/docs`。浏览器与 API 同源，所有用户文案为简体中文。写请求检查 Origin；生产 Cookie 使用 Secure、HttpOnly 和 SameSite=Lax。

## 会话与任务

先 `POST /api/session` 建立匿名会话。服务端 Cookie 用于认证，`X-Session-Scope` 仅是本地草稿隔离的非认证标识，不可用来访问资源。

v1 的完整请求/响应定义见 [OpenAPI](../contracts/openapi.json)：

1. `POST /api/tasks` 创建任务；`PATCH /api/tasks/{taskId}` 修改草稿，必须提交 `expectedRevision`。
2. `POST /api/tasks/{taskId}/analyze` 分析内容；生成请求使用 UUID 格式的 `Idempotency-Key`，返回 202 和 job。
3. `GET /api/jobs/{jobId}` 查询实际状态；相同幂等键和请求返回原 job，改变请求内容会冲突。
4. `POST /api/tasks/{taskId}/confirm` 提交当前版本全部事实 ID 的确认，随后可调用 `/render`。
5. `GET /api/tasks/{taskId}/export?format=png|jpg&expectedRevision=N` 下载当前成功预览。

内容修改会增加 revision、使旧确认和预览失效。生成绑定不可变输入快照，旧任务结果不会覆盖新版本。资源按会话或账号工作区隔离，无法访问其他用户的任务、作业和原图。

## v2 扩展

[v2 接口描述](api-v2.json) 保留本地实现新增的 40 个路径。重要分组如下：

| 路径 | 用途 |
| --- | --- |
| `/api/v2/auth/*` | 开发验证码、注册、登录、退出、找回/修改密码、昵称及管理员用户管理 |
| `/api/v2/tasks/*` | 活动作业恢复、历史重命名/复制/删除、逐对象排版编辑 |
| `/api/v2/tasks/{taskId}/export` | PNG/JPG/PDF，`scale=1|2` 和当前 `expectedRevision` |
| `/api/v2/materials/*` | 私有/公共素材、缩略图、收藏、裁剪、生成和非作者审核 |
| `/api/v2/template-drafts/*` | 模板编辑、单层网格与合并跨度、试填、提交及审核 |
| `/api/v2/published-templates/*` | 不可变发布模板目录、详情和缩略图 |
| `/api/v2/jobs/*`、`/api/v2/notifications/*` | 实际排队位置与完成通知 |
| `/api/v2/admin/*` | 管理员运行诊断及空队列时切换已配置的模型模式 |

开发验证码只在本地开发模式显示，未发送邮件或短信；生产环境拒绝此模式。管理员没有默认密码，只能通过交互式 `scripts/create_admin.py` 创建。作者不能审核自己的素材或模板，普通注册账号不能自助升权。

## 图片与模型边界

- 上传仅接收经实际解码验证的 JPG/PNG；≤10 MiB、≤4000 万像素；拒绝 SVG、远程图片 URL 和扩展名伪装。
- 默认文字/生图 Mock 明显标注。real 缺配置时明确报错，不返回 Mock 冒充成功。
- 大模型仅返回白名单 JSON；事实保护和字段长度由后端检查，渲染文字使用转义文本节点。
- 图像真实适配仅接受单张 Base64 图片并再次解码；当前真实图生图供应商协议尚未配置。
- 字体和图片内嵌到排版文档，渲染浏览器禁止外部网络请求。
- PDF 为当前预览的栅格封装；2×为普通像素缩放，没有矢量或无损增强承诺。

详细运行环境与配置见 [开发说明](DEVELOPMENT.md) 和 [部署说明](DEPLOYMENT.md)。
