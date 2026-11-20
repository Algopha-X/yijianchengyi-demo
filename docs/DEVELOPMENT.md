# 开发与测试

## 依赖和目录

后端要求 **Python 3.12**（项目约束为 `>=3.12,<3.13`），通过 uv 和 `backend/uv.lock` 安装；前端使用 Node.js 22、npm 和 `frontend/package-lock.json`。渲染及浏览器 E2E 还需要 Playwright Chromium。首次安装需要联网。

2026-11-20 的 `npm audit` 报告 19 项依赖告警（16 项中等、1 项高、2 项严重），生产依赖审计为 0 项。当前未升级依赖或改写源锁文件；这个公开快照尚未通过生产安全验收，审计详情见 [验证记录](VALIDATION.md)。后续依赖升级应单独评估兼容性并复跑检查，不能把源码一致性校验当作安全验证。

| 目录或文件 | 用途 |
| --- | --- |
| `backend/app/`、`backend/migrations/` | API、账号、队列、存储、模型适配、图片处理与真实排版 |
| `frontend/src/` | Vue 页面、草稿恢复、字段确认、素材与模板工作台 |
| `templates/`、`examples/` | 经校验的模板与完整/缺失演示输入 |
| `contracts/` | v1 API、模板 schema 和数据库结构 |
| `assets/fonts/` | 内嵌中文字体、来源和 OFL 许可，渲染时不下载外部字体 |
| `backend/tests/`、`frontend/tests/` | 业务回归测试与前端单测 |
| `tests/e2e/` | 面向运行中服务的正式浏览器测试 |
| `scripts/` | 启动、HTTP 验证、管理员创建、备份及快照检查 |
| `data/` | 运行时数据库、图片和缩略图；自动生成，不随源码发布 |

v1 生成类型保留于 `frontend/src/types/api.d.ts`；v2 的显式类型扩展在 `frontend/src/api/`。运行中的完整接口入口为 `/api/docs` 和 `/api/openapi.json`，另见 [API 说明](API.md)。

## Windows：统一本机服务

从仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

脚本显式选用文字 Mock、图片 Mock、开发验证码，只监听 `127.0.0.1`；先构建前端，再生成缺失缩略图，最后启动后端并提供前端页面。无需 API Key。安装完成后的启动可加 `-SkipInstall`，端口冲突可加 `-Port 8001`。

## Linux：手动启动同一套服务

以下从仓库根目录开始，所有模式在当前终端中显式设为本地演示。若已有服务占用 8000，先选择独立端口和对应的允许来源。

```sh
cd frontend
npm ci
npm run build
cd ../backend
uv sync --frozen
uv run --frozen playwright install --with-deps chromium

export LLM_MODE=mock IMAGE_MODE=mock VERIFICATION_MODE=development
export APP_ENV=development COOKIE_SECURE=false
export APP_ALLOWED_ORIGINS=http://127.0.0.1:8000
export DATA_DIR=./data DATABASE_PATH=./data/app.db
uv run --frozen python -m app.rendering --missing --directory ../data/template-previews
uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`--with-deps` 安装浏览器所需的系统库，可能需要系统管理员权限。打开 http://127.0.0.1:8000。停止服务不会删除 `data/`。

## 前端热更新开发

先按上面的后端步骤安装依赖、生成缩略图并启动 8000。在另一个终端的 `frontend/` 运行：

```sh
npm ci
npm run dev -- --host 127.0.0.1
```

打开 http://127.0.0.1:5173。开发代理固定指向 8000；后端允许来源须包含 `http://127.0.0.1:5173`，可参考根目录 `.env.example`。相对数据路径由后端按仓库根目录解析。

Windows PowerShell 若拦截 `npm.ps1`，使用 `npm.cmd` 执行相同命令；统一启动脚本已经采用 `npm.cmd`。

## 可复现检查

以下是实际测试入口。2026-11-20 在公开源码目录复跑前端 21 项、浏览器 6 项；后端 343 项是 2026-11-17 的历史验证，本轮未重跑。数量随代码变化，新的环境应以自己的命令输出为准。公开快照不含原项目的成员计分或内部验收检查。

后端（在 `backend/`）：

```sh
uv sync --frozen
uv run --frozen playwright install chromium
uv run --frozen pytest -q
uv run --frozen ruff check app tests
```

后端测试使用临时 SQLite、模拟传输和真实本地排版。依赖模板、schema、演示输入、字体，以及 `scripts/backup_data.py` 和 `scripts/create_admin.py`；不需要成员台账、外部供应商密钥或历史证据目录。不要用生产环境变量运行测试。

前端（在 `frontend/`）：

```sh
npm ci
npm run typecheck
npm run lint
npm run test:unit -- --run
npm run build
```

浏览器 E2E：先启动已构建的 Mock 服务，再在另一个终端的 `backend/` 运行：

```sh
uv run --frozen pytest ../tests/e2e/test_demo_browser.py -q
```

这 6 项覆盖上传、PNG/JPG 实际解码和尺寸、刷新、缺失补齐、自动保存、多标签冲突、移动视口重试、公众号配图导出，以及预览加载失败后重新生成恢复。每项创建独立浏览器 context，不操作已打开的用户页面；会在所测服务中创建自己的测试数据，产物默认写入 `data/browser-validation/`。更换地址可设置 `DEMO_BASE_URL`，例如 Linux 的 `DEMO_BASE_URL=http://127.0.0.1:8001 uv run --frozen pytest ../tests/e2e/test_demo_browser.py -q`。设置 `DEMO_ARTIFACTS_DIR` 可将截图、下载及记录写入独立目录；建议使用独立测试服务和数据目录。

HTTP 流程验证和导出快照校验也从 `backend/` 执行：

```sh
uv run --frozen python ../scripts/verify_demo.py
uv run --frozen python ../scripts/check_snapshot.py
```

HTTP 验证默认要求 Mock，使用新会话创建测试任务并保存记录。快照校验只核对发布清单与源文件，不证明业务功能或真实外部调用已通过。

## 常见问题

- **端口占用：** 统一启动脚本使用 `-Port`；前端热更新代理仍指向 8000，修改端口时同步调整代理和允许来源。
- **浏览器或系统库缺失：** 在后端环境安装 Chromium；Linux 补齐系统库后重试。启动脚本可显式选择已安装的 Chrome/Edge 通道，但这不等于完成跨浏览器验收。
- **模板缩略图缺失：** 在 `backend/` 重新运行 `uv run --frozen python -m app.rendering --missing --directory ../data/template-previews`，仅补缺失缓存。
- **会话或网络失效：** 使用页面的重试/重新连接。新稿先保存在浏览器；清理 Cookie、站点存储或运行目录前，先保存重要内容。
- **真实模式配置错误：** 默认恢复 Mock 演示即可继续本地开发；真实服务配置和限制见 [部署说明](DEPLOYMENT.md)。
