# 本地开发基线

日期：2026-09-05 · 关联 [COL-15](https://linear.app/colife/issue/COL-15) · 分支 `codex/col-15-development-baseline`。

## 范围与选择

用户要求同步仓库并开始开发，论文样本后续选择 Agent 相关论文。先落实不依赖选文的运行基础，方便逐步实现上传、解析和持久化。P0 的真实需求、人工证据和问答规则仍待完成。

- Python 3.12.12：本机已有，使用项目独立虚拟环境，避免依赖系统默认 Python 3.14。
- FastAPI：沿用项目既有建议，提供带响应结构和 OpenAPI 文档的 HTTP 入口。
- uv 与 `uv.lock`：固定实际依赖及哈希，安装、启动、检查统一使用 `--locked`。
- 包索引在 `pyproject.toml` 显式配置为清华镜像，与本机已有使用习惯一致，使锁文件来源不依赖个人全局配置；CI 需能访问该镜像。
- `src/papertrail`：按可安装的 Python 包组织，为后续业务模块提供入口。
- 本地使用 `127.0.0.1`；配置只包含监听地址和端口。当前没有数据库连接或模型调用。

| 组件 | 本次验证版本 |
| --- | --- |
| Python | 3.12.12 |
| uv | 0.10.0 |
| FastAPI | 0.141.1 |
| Uvicorn | 0.52.4 |
| Pydantic | 2.13.5 |
| Starlette | 1.6.0 |
| HTTPX2（检查用） | 2.12.0 |
| Ruff | 0.16.6 |

精确依赖以 [uv.lock](../uv.lock) 为准。本次随 FastAPI 安装的 Starlette 要求测试客户端优先使用 HTTPX2；检查依赖据此选用 HTTPX2，避免旧 HTTPX 的弃用提示。

PostgreSQL、PDF 解析库、pgvector、前端、模型服务和部署工具在对应功能任务中选择并验证。本机当前 PATH 未找到 Docker；当前基础不依赖 Docker，持久化任务开始前再决定数据库运行位置。

## 使用方式

在仓库根目录执行：

```bash
make setup
make check
make dev
```

`make setup` 安装锁定依赖，并仅在不存在时复制 `.env.example` 为 `.env.local`。`make dev` 加载该配置并启动带重载的开发服务；按 Ctrl+C 停止。端口冲突时修改 `.env.local` 的 `UVICORN_PORT` 后重新启动。

访问 `/health` 可确认服务进程响应；`/docs` 提供交互式 API 文档，`/openapi.json` 提供接口结构。健康状态不代表数据库可用、PDF 已解析或模型可调用。

`make check` 执行锁文件一致性安装、Ruff 静态检查与格式检查、已安装应用的 HTTP/OpenAPI 冒烟检查及 `git diff --check`。CI 使用同一命令，运行于 Ubuntu；工作流已配置，本次只记录本地运行结果。

配置、虚拟环境、PDF 原件和 `data/` 运行数据由 `.gitignore` 排除；`.env.example` 与锁文件应纳入版本控制。今后样例保留来源、版本和必要核对记录，是否提交原文按具体来源决定。

## 已执行的验证

- 从空项目虚拟环境执行锁定安装，项目以可安装包形式构建成功。
- `make check` 通过：静态检查、格式、HTTP/OpenAPI 冒烟和 Git 空白检查。
- `make dev` 真实启动于 `127.0.0.1:8000`；HTTP `/health` 返回 `200` 与 `{"status":"ok"}`，`/docs` 和 `/openapi.json` 返回 `200`。
- `git check-ignore` 确认 `.env.local`、`.env.production`、`.venv/`、`data/` 和 PDF 文件被排除；`.env.example` 可被 Git 跟踪。
- 已审查新增运行代码、依赖声明和工作流；应用仅包含存活接口，没有外部模型调用或数据持久化路径。

验证范围：本地 macOS / Apple Silicon。未运行远端 GitHub Actions、Linux 环境、数据库、PDF 解析、模型评测或用户学习验收；不能将这些结果记为产品可用或阶段完成。

## 开发入口与下一步

[服务入口](../src/papertrail/main.py)只定义 HTTP 接口；[冒烟检查](../scripts/smoke.py)使用已安装的应用核对响应和 OpenAPI。[产品说明草稿](product-brief.md)记录已知需求及待补材料。

下一项围绕 PDF 文档链路写清文件约束、空正文与解析失败状态、页码关系及持久化方式，再实现最小可验证流程。选定 Agent 论文后补人工证据，用于验证解析位置和后续问答质量。

实现参考：[FastAPI 入门](https://fastapi.tiangolo.com/tutorial/first-steps/)、[uv 项目管理](https://docs.astral.sh/uv/guides/projects/)、[uv GitHub Actions 集成](https://docs.astral.sh/uv/guides/integration/github/)。
