# 本地开发与运行

## 当前功能（COL-16）

本地 PDF 导入、持久保存、逐页核对已实现。关联分支 `codex/col-16-pdf-import`；[行为规格](pdf-import.md)、[实际验证记录](verification-col-16.md)。下方 COL-15 记录保留为历史。

### 准备与启动

需要 Make、uv、Node.js 22.13+、PostgreSQL 17 原生命令。macOS 安装 PostgreSQL：`brew install postgresql@17`。本机已安装该版本；未启用 Homebrew 数据库登录服务。其他安装位置可通过 `PAPERTRAIL_PG_BIN` 指定包含 `initdb` 与 `pg_ctl` 的目录。

在仓库根目录运行：

```bash
make setup
make check
make dev
```

`make setup` 根据 `uv.lock` 与 `web/package-lock.json` 安装依赖，仅在缺失时创建 `.env.local`。`make dev` 构建网页、启动专用数据库，再启动 FastAPI。访问 <http://127.0.0.1:8000/>。按 Ctrl+C 停止应用；`make db-stop` 停止数据库。下次 `make dev` 继续使用已保存论文。

本地 PostgreSQL 仅监听 `127.0.0.1:55432`，数据位于 `data/postgres/`，角色和数据库名均为 `papertrail`，本地开发使用 trust 认证。该配置用于当前单用户开发，不作为多人服务配置。脚本不会启动或停止其他项目的数据库。首次安装 Homebrew 创建的默认集群并未用于本项目。

### 配置与存储

`.env.example` 列出监听地址、数据库连接、数据目录、大小/页数/时限。已有 `.env.local` 不覆盖，缺失的 PaperTrail 配置使用相同默认值。修改监听端口使用 `UVICORN_PORT`。

- `data/library/papers/` 保存 PDF 原件，系统 UUID 命名；`staging/` 用于上传临时文件。
- PostgreSQL 保存文件哈希、原始名称、解析版本和逐页文本；使用版本 1 的 SQL 建表，启动时幂等执行。
- 如需备份，保留完整数据库及 `data/library/` 两部分。只拷贝 PDF 不会带回页面记录，删除 `data/` 会丢失论文及数据库。
- `data/`、PDF、配置、虚拟环境、网页产物及浏览器证据不提交 Git；锁文件和结构 SQL 纳入 Git。

### 前端与检查

生产网页由 FastAPI 同源提供；PDF.js 的 worker、字符映射、字体与图片解码资源均随构建放在本地，无 CDN 依赖。阅读器只渲染页面，提取文字作为文本显示。

开发前端时可在另一个终端运行 `npm run dev --prefix web`，默认代理 `/api` 到 `127.0.0.1:8000`。若后端端口改变，需相应修改 `web/vite.config.ts`；日常用 `make dev` 无需单独启动前端。

`make check` 运行 Ruff、前端类型检查与生产构建、18 项业务测试、HTTP/OpenAPI 冒烟和 `git diff --check`。本地测试自动启动临时 PostgreSQL 集群并清理；不会清空开发论文库。CI 使用 PostgreSQL 17 服务，检查脚本创建独立 UUID 命名数据库再删除它；需 `PAPERTRAIL_TEST_ADMIN_URL` 指向专用测试服务。CI 是否通过以实际远端运行记录为准。

学习入口：`src/papertrail/ingestion.py` 解释文件怎样进入系统；`repository.py` 解释文件与数据库的事务边界；`web/src/main.tsx` 解释页面怎样发起请求、读取逐页结果。正文/图表质量限制见验证记录。

## COL-15 初始服务基线（历史）

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
