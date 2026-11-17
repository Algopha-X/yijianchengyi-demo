# 部署准备与运行数据

本仓库公开的是源码，**没有随仓库发布一个已经上线的公网服务**。当前可复现目标为本机 Mock 演示；没有本次真实云环境、域名证书、模型供应商或邮件/短信的外部调用验收记录。

## 本机演示

Windows 从仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

打开 http://127.0.0.1:8000。后续可加 `-SkipInstall`，端口冲突可加 `-Port 8001`。脚本显式设置文字 Mock、图片 Mock 和开发验证码，不需要密钥。Linux 手动启动见 [开发说明](DEVELOPMENT.md)。

启动前会由 Chromium 生成缺失模板的真实缩略图，已有缓存不覆盖。数据库、图片解码、裁剪、排版、导出和密码散列均实际执行；Mock 仅替代尚未配置的外部处理。开发验证码显示在页面，未发送邮件或短信。

## Docker Compose 本机准备

仓库保留 Dockerfile 和两份 Compose 配置。以下为部署复现步骤，本次发布不据此声称已经完成 Docker 构建或公网部署。

```sh
docker compose build
docker compose run --rm --no-deps app python -m app.rendering --missing --directory /app/data/template-previews
docker compose up -d
```

本机配置只将端口绑定到 `127.0.0.1`，数据保存在命名卷中。缩略图来自真实模板和固定试填文字，不涉及模型调用。避免用 `docker compose down -v` 或删除数据目录作为升级方式。

## 生产单机配置

1. 准备 Linux 主机、域名、TLS 证书和 Docker Compose。当前使用 SQLite 与本地文件，应用只运行一个进程；进程内部默认两个生成 worker，不能把增加 Uvicorn worker 视为容量已达标。
2. 将 `deploy/production.env.example` 复制到根目录 `.env.production`，在服务器本地填写实际配置。密钥只留在后端环境变量，禁止放入 `VITE_*`、前端、截图或仓库。
3. `APP_ALLOWED_ORIGINS` 设置为实际 HTTPS 完整 origin，不带路径；生产 Cookie 使用 Secure、HttpOnly、SameSite=Lax。生产禁止开发验证码，需配置邮件服务。
4. 审阅 Compose 配置，再构建、补缩略图、启动：

   ```sh
   docker compose -f compose.production.yaml config
   docker compose -f compose.production.yaml build
   docker compose -f compose.production.yaml run --rm --no-deps app python -m app.rendering --missing --directory /app/data/template-previews
   docker compose -f compose.production.yaml up -d
   ```

5. 按 `deploy/nginx.conf.example` 替换域名和证书路径，使用 TLS 反向代理。先运行 `nginx -t`，确认配置正确后再加载。
6. 配置真实服务后，实际验证注册、找回密码、用户隔离、上传、生成、导出、任务失败、断网恢复、服务重启及备份恢复。配置已填写不等于供应商调用已成功。

## 外部服务的真实边界

| 服务 | 配置与当前边界 |
| --- | --- |
| 文字模型 | 后端 HTTPS Chat Completions 兼容 JSON 接口；需 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` |
| 真实文生图 | HTTPS `/images/generations` 的 `b64_json` 返回；拒绝远程图片 URL，实际解码后进入图库 |
| 真实图生图 | 供应商协议尚未确定，当前 real 调用明确报配置错误 |
| 邮箱验证码 | `VERIFICATION_MODE=smtp` 并配置 `SMTP_*`；使用 TLS-on-connect，示例端口为 465 |
| 手机验证码 | 生产短信供应商尚未接入，手机号生产验证会明确报未配置 |

默认 `LLM_MODE=mock`、`IMAGE_MODE=mock`。真实模式缺配置时会报错，不会伪装 Mock 为真实调用。已有适配器测试使用模拟传输和失败响应，不能替代真实供应商验证。管理员运行模式切换只改变当前进程配置，要求队列空闲，重启后恢复环境变量设置。

## 管理员与审核

没有默认管理员账号或密码。服务首次运行并初始化数据库后，在 `backend/` 的另一个终端执行：

```sh
uv run --frozen python ../scripts/create_admin.py
```

按交互提示输入账号地址和两次密码，密码不回显。命令默认使用“系统管理员”昵称，如需指定可加 `--nickname`。普通注册不会提权；公共素材和模板必须由另一位审核员或管理员审核，作者不能自审。

## 备份与恢复

本机数据库及图片位于 `data/`，生产配置使用持久卷。只备份数据库文件可能得到不一致状态，仓库提供 SQLite backup API 与引用图片复制工具。

从 `backend/` 运行，使用一个尚不存在的唯一目标目录：

```sh
uv run --frozen python ../scripts/backup_data.py --output ../backups/first-backup
```

工具复制快照实际引用的不可变图片，记录 SHA-256；缺图或路径越界会失败，未完成备份的 manifest 标记为 `incomplete`。备份包含私有作品、账号信息和密码散列，应限制访问。保留旧版本及配置，恢复先在独立数据目录验证 manifest 和文件完整性，再切换服务路径。

单机账号跨会话保存不等于云存储或异地备份。容量、响应时间、真实浏览器兼容和完整原需求差距见 [功能说明](FEATURES.md)。
