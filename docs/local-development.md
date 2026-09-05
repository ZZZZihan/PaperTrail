# 本地开发与运行

## 当前功能（COL-19 研读质量 v0.2）

单篇文本 PDF 支持上传、持久保存与逐页核对。问答采用问题要点整理、中文查询转换、BM25 检索、结构化生成、引用校验，以及同一次独立 AI 调用中的事实支持和覆盖检查；有据但不完整时保存 `partial_answer` 与缺失项。简介按需生成研读卡，解释术语、研究问题、方法输入/过程/输出和实验条件，区分论文陈述、作者解释、教学示意和系统推断。两条链路共用原文、引用跳页及原调用账本。

当前分支 `codex/col-19-paper-introduction-demo`，任务为 In Progress，PR 为 draft、未合并。代码检查与效果验收分开：一次 `make check` 的 299 项后端测试通过；新 react-03 真实运行仍有模型冻结与人工轨迹条件遗漏，已标部分回答，修复继续。新 ReAct v5 简介运行因总期限内末次检查超时失败，旧卡继续可读，新卡待再验证。范围见 [质量规格](reading-quality-v02.md) 与 [研读卡规格](paper-introduction-demo.md)，实际版本、运行及用量见 [v0.2 验证记录](verification-quality-v02.md)。

本机已配置真实 `gpt-5.6-sol`，已获准的 provider_quota 原 scope 累计上限 160 次；本轮开始前快照 153/160 不代表实时余量，实际费用未知。优先按 [简短人工清单](manual-trial-v02.md) 核对已保存结果。旧 [问答 v0.1 规格](evidence-qa-v01.md)、[开发诊断](development-diagnostics.md)、[简介 v4 验证](verification-col-19.md)、COL-16 的 [PDF 行为](pdf-import.md) 和 [验证](verification-col-16.md) 保留历史证据，下方 COL-15 记录同样为历史。

### 准备与启动

需要 Make、uv、Node.js 22.13+、PostgreSQL 17 原生命令。macOS 安装 PostgreSQL：`brew install postgresql@17`。本机已安装该版本；未启用 Homebrew 数据库登录服务。其他安装位置可通过 `PAPERTRAIL_PG_BIN` 指定包含 `initdb` 与 `pg_ctl` 的目录。

在仓库根目录运行：

```bash
make setup
make check
make serve
```

`make setup` 根据 `uv.lock` 与 `web/package-lock.json` 安装依赖，仅在缺失时创建 `.env.local`。`make serve` 构建网页、启动专用数据库，再启动单进程 FastAPI。访问 <http://127.0.0.1:8000/>。按 Ctrl+C 停止应用；`make db-stop` 停止数据库。下次 `make serve` 继续使用已保存论文与问答。`make dev` 适合修改代码，自动重载会中断正在处理的问答。一个数据库仅允许一个应用进程，防止重复启动误恢复正在执行的任务。若数据库意外重启导致原 session 丢失，应用会拒绝新问题及后续模型调用，必须重启应用后继续；历史和原文仍可读取。

本地 PostgreSQL 仅监听 `127.0.0.1:55432`，数据位于 `data/postgres/`，角色和数据库名均为 `papertrail`，本地开发使用 trust 认证。该配置用于当前单用户开发，不作为多人服务配置。脚本不会启动或停止其他项目的数据库。首次安装 Homebrew 创建的默认集群并未用于本项目。

### 配置与存储

`.env.example` 列出监听地址、数据库连接、数据目录、大小/页数/时限。已有 `.env.local` 不覆盖，缺失的 PaperTrail 配置使用相同默认值。修改监听端口使用 `UVICORN_PORT`。

- `data/library/papers/` 保存 PDF 原件，系统 UUID 命名；`staging/` 用于上传临时文件。
- PostgreSQL 保存文件哈希、原始名称、解析版本、逐页文本、问题/回答/引用及模型调用账本；版本 2 增量添加问答和账本表，版本 3 加入简介任务及请求别名，版本 4 增加 coverage 与 partial_answer；保留既有论文、任务和调用。
- 如需备份，保留完整数据库及 `data/library/` 两部分。只拷贝 PDF 不会带回页面记录，删除 `data/` 会丢失论文及数据库。
- `data/`、PDF、配置、虚拟环境、网页产物及浏览器证据不提交 Git；锁文件和结构 SQL 纳入 Git。

### 接入真实模型与费用

在本机编辑被 Git 忽略的 `.env.local`，补齐 `.env.example` 中的以下项，然后重启应用：

| 配置 | 含义 |
| --- | --- |
| `PAPERTRAIL_MODEL_BASE_URL` | OpenAI 兼容服务根路径，例如 `https://api.deepseek.com`；客户端追加 `/chat/completions` |
| `PAPERTRAIL_MODEL_ALLOW_HTTP_ORIGIN` | 可选，明确允许一个 HTTP 中转入口，例如 `http://192.168.1.2:8080`；默认空，不自动开放其他 HTTP 地址 |
| `PAPERTRAIL_MODEL_NAME` / `PAPERTRAIL_MODEL_API_KEY` | 获准的模型名称与本地密钥 |
| `PAPERTRAIL_MODEL_PROFILE` | 显式请求参数方案：默认 `compatible`，或 `openai`；不按模型名称猜测 |
| `PAPERTRAIL_MODEL_BUDGET_MODE` | 默认 `priced`，按金额和单价控制；明确选择 `provider_quota` 表示授权使用已有供应商额度 |
| `PAPERTRAIL_MODEL_MAX_CALLS` | `provider_quota` 必填，1—1000 的整数；当前 scope 全部已记录调用累计上限 |
| `PAPERTRAIL_MODEL_BUDGET` / `PAPERTRAIL_MODEL_CURRENCY` | `priced` 必填的获准金额与币种；`provider_quota` 无需金额，币种为空时采用 USD，必须与同 scope 既有记录一致 |
| `PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION` / `PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION` | 同币种每百万 token 保守输入/输出单价，用于调用前预留及用量后估算 |
| `PAPERTRAIL_MODEL_BUDGET_SCOPE` | 本轮账本范围，默认 `v01-development`；不要通过换值绕过已经消耗的额度 |

每题最多三次固定调用，单次默认 45 秒，总期限 180 秒；输出默认限制 1800 tokens。`PAPERTRAIL_MODEL_THINKING` 默认空，仅在服务支持时设 `disabled` 或 `enabled`。本轮建议非推理模式，避免思考 token 占用结构化输出额度。服务必须支持 JSON 输出；未配置时可浏览 PDF 与历史，不生成模拟答案。

论文简介另用至少 90 秒的单次窗口和至少 5,000 tokens 输出上限，总期限仍为 180 秒；正常两次调用，发生一次内容修订时最多四次，全部进入同一账本。刷新、查看成功缓存、重新点击已有成功简介均不再调用模型。旧格式成功简介可经用户主动“补全为研读卡”新建任务，POST 使用新的 request_id 和 refresh_if_outdated: true；当前 paper-reading-card-v1 成功结果仍复用。升级中或失败时接口通过 previous_introduction 保留旧卡可读，失败后主动重试创建新任务。同一 request_id 始终指向原任务，网络提交不确定时先确认原提交结果。

`compatible` 请求使用 `max_tokens`、`temperature: 0`，并在配置非空时附带服务的 `thinking` 参数。`openai` 请求使用 `max_completion_tokens` 和 `reasoning_effort: "none"`，省略 `temperature` 与 `thinking`；此方案下 `PAPERTRAIL_MODEL_THINKING` 必须留空，否则视为配置无效。两种方案都使用 Chat Completions、JSON 对象输出及 `stream: false`，内部输出上限与预算预留仍使用同一个 `PAPERTRAIL_MODEL_MAX_OUTPUT_TOKENS`。配置必须与所选服务和模型的实际参数支持一致；程序不会根据模型前缀切换方案，也不会在请求失败后自动换参数重试。运行追踪保存所选方案、实际输出参数名、推理强度和温度；未发送的温度记录为 `null`。

HTTPS 和 `http://127.0.0.1` / `http://localhost` 沿用默认支持。若用户指定局域网 HTTP 中转站，例如服务根路径为 `http://192.168.1.2:8080/private-route/v1`，则将 `PAPERTRAIL_MODEL_ALLOW_HTTP_ORIGIN` 设为 `http://192.168.1.2:8080`。允许项只接受单个 origin，不含用户信息、查询参数、片段或业务路径（末尾单个 `/` 可省略）；协议、主机和有效端口必须与服务根路径一致，未写 HTTP 端口时按 80 匹配。该设置只允许这个明确入口，不接受地址列表或通配符，也不因地址属于局域网而自动允许。运行追踪仅保存服务 origin 和完整根路径的哈希，不保存业务路径或密钥。

`priced` 模式每次调用前在持久账本预留输入保守上界及最大输出费用；成功返回用量后按配置单价估算，网络失败且用量未知则保留预留额。费用是配置单价计算的估算，不是供应商账单；特殊费用、不同计费规则或错误单价可能导致偏差。应使用普通文本 token 计费模型和供应商侧额度限制。服务、价格、额度得到用户授权后再运行真实诊断。

当用户明确授权使用已有中转额度、而所选模型费率无法核实时，可显式选择 `provider_quota` 并设置 `PAPERTRAIL_MODEL_MAX_CALLS`；此模式不读取金额或单价，不估造模型费用。它在相同持久账本和事务锁下预留一次调用名额，累计当前 scope 的全部历史行，包含之前的 `priced` 调用、失败调用及未完成预留；重启或换模型、模式不会重置次数。达到上限后拒绝下一次调用，无自动重试或自动更换 scope。供应商余额限制仍由供应商执行，本地调用次数上限不是美元费用上限。

额度模式中的 `reserved_cost: 0` 仅为调用名额占位，绝不表示免费；`actual_cost` 和逐次 `estimated_cost` 始终为 `null`，即使用量 tokens 已知也不估费，来源记录为 `unknown_provider_rates`。快照中的 `known_cost_subtotal` 只累计已知金额；全部调用费用未知时它为 `0`、`estimated_cost` 为 `null`，并逐次计入 `unknown_cost_calls`。同 scope 已有额度模式调用后，不能切回金额模式将这些未知费用当作零继续预留。

固定来源准备（零模型调用）：

```bash
uv run --locked python scripts/fetch_diagnostic_papers.py
uv run --locked python scripts/fetch_diagnostic_papers.py --verify-only
uv run --locked python scripts/evaluate_development.py --prepare-only
```

真实诊断通过运行中的应用 API 发起，使用相同持久账本。先检查实际余量和当前活跃任务，按冻结来源选择代表性样例，不将下面的复现命令当作要求立即重复运行：

```bash
uv run --locked python scripts/evaluate_development.py --run --ids react-03
uv run --locked python scripts/evaluate_introductions.py --run --ids react --refresh-if-outdated
```

问答结果写入不复用的 `data/diagnostics/runs/`，简介写入 `data/diagnostics/introduction-runs/`。默认问答 `--run` 会执行原开发集全量，仅在明确有足够已授权余量时安排；本轮未执行的全量回归和候选留出评测不能记为完成。旧来源与复现规则见 [开发诊断说明](development-diagnostics.md)，当前执行证据见 [v0.2 验证记录](verification-quality-v02.md)。

### 前端与检查

生产网页由 FastAPI 同源提供；PDF.js 的 worker、字符映射、字体与图片解码资源均随构建放在本地，无 CDN 依赖。阅读器只渲染页面，提取文字作为文本显示。

开发前端时可在另一个终端运行 `npm run dev --prefix web`，默认代理 `/api` 到 `127.0.0.1:8000`。若后端端口改变，需相应修改 `web/vite.config.ts`；日常用 `make dev` 无需单独启动前端。

`make check` 运行 Ruff、前端类型检查与生产构建、业务测试、HTTP/OpenAPI 冒烟和 `git diff --check`。本地测试自动启动临时 PostgreSQL 集群并清理；不会清空开发论文库。CI 使用 PostgreSQL 17 服务，检查脚本创建独立 UUID 命名数据库再删除它；需 `PAPERTRAIL_TEST_ADMIN_URL` 指向专用测试服务。CI 是否通过以实际远端运行记录为准。

学习入口：`src/papertrail/coverage.py` 解释问题要点怎样与已发布事实关联；`introduction.py` 解释支持、来源类别和覆盖怎样分别检查；`src/papertrail/ingestion.py` 解释文件怎样进入系统；`repository.py` 解释文件与数据库的事务边界；`web/src/main.tsx` 解释页面怎样发起请求、读取逐页结果。正文/图表质量限制见验证记录。

## COL-15 初始服务基线（历史）

日期：2026-09-05 · 关联 [COL-15](https://linear.app/colife/issue/COL-15) · 分支 `codex/col-15-development-baseline`。

## 范围与选择

用户要求同步仓库并开始开发，论文样本后续选择 Agent 相关论文。先落实不依赖选文的运行基础，方便逐步实现上传、解析和持久化。P0 的真实需求、人工证据和问答规则仍待完成。

- Python 3.12.12：本机已有，使用项目独立虚拟环境，避免依赖系统默认 Python 3.14。
- FastAPI：沿用项目既有建议，提供带响应结构和 OpenAPI 文档的 HTTP 入口。
- uv 与 `uv.lock`：固定实际依赖及哈希，安装、启动、检查统一使用 `--locked`。
- COL-15 最初在 `pyproject.toml` 显式使用清华镜像。COL-18 远端 CI 下载被镜像返回 403 后，已改为官方 PyPI；包版本和文件哈希保持不变，避免运行环境依赖区域镜像的访问策略。
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
