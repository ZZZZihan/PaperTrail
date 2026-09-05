# PaperTrail

AI Native 论文研读 Agent：辅助论文理解、基于原文证据的问答与阅读笔记，作为 Agent 全栈开发学习与求职实践项目。

**当前阶段：已实现首个本地功能，待用户验收。** 可上传文本 PDF、保存原件与逐页文字，在网页中对照原 PDF 和提取结果；支持哈希去重、空白页保留与明确失败提示。P0 真实阅读需求、人工问答样例、模型问答与部署仍待完成。

## 本地启动

需要 uv、Make、Node.js 22.13+ 与 PostgreSQL 17。macOS 可执行 `brew install postgresql@17`；数据库由项目脚本管理，不需要启用 Homebrew 登录服务。Python 版本由 `.python-version` 固定。

```bash
make setup
make check
make dev
```

`make setup` 安装锁定的 Python 与网页依赖，在不存在时复制 `.env.example`。`make dev` 构建网页、启动专用本地 PostgreSQL（127.0.0.1:55432），再启动应用。

- **网页：<http://127.0.0.1:8000/>**，上传 PDF 后进入逐页核对。
- 数据保存于忽略目录 `data/library/` 和 `data/postgres/`；刷新或重启后保留。
- 在运行终端按 Ctrl+C 停止应用，`make db-stop` 停止项目数据库；下次 `make dev` 继续使用原有数据。

- 存活检查：<http://127.0.0.1:8000/health>，返回 `{"status":"ok"}`。
- 开发接口文档：<http://127.0.0.1:8000/docs>。
- OpenAPI：<http://127.0.0.1:8000/openapi.json>。

`/health` 仅表示存活。实际文档功能、限制与验证见 [PDF 导入规格](docs/pdf-import.md)、[本地开发说明](docs/local-development.md)和[本轮验证记录](docs/verification-col-16.md)。当前无需模型密钥。

## 目标与第一版

围绕本人阅读 LLM / Agent 论文的真实需求，做出能使用、能核对、能评测、能部署的应用，同时掌握并解释全栈开发的核心实现。

第一版用户任务：上传一篇可提取正文的 PDF → 提出问题 → 获得答案、原文证据与 PDF 页码 → 核对引用；证据不足和处理失败均有明确状态。先完成固定检索问答，再按实际收益引入受控 Agent，之后扩展 2—5 篇论文比较。

PaperTrail 是独立的产品与求职项目；学术选题、开题和发表研究由 mypaper 承载，RepoPilot 也独立维护。

## Codex 从这里接手

1. 阅读 [AGENTS.md](AGENTS.md) 和 [交接状态](docs/handoff.md)。
2. 阅读 [项目与学习基线](docs/project-baseline.md)、[开发流程](docs/development-workflow.md)。
3. 连接 [Linear 项目](https://linear.app/colife/project/papertrail-论文研读-agent-93a0ba2ee811)，核对当前任务和依赖；[任务快照](docs/linear-snapshot.md)仅作交接参考。
4. 首个功能关联 [COL-16](https://linear.app/colife/issue/COL-16)。用户提出的自动查找、下载论文已记录 [COL-17](https://linear.app/colife/issue/COL-17)，尚未实现。[COL-10](https://linear.app/colife/issue/COL-10)真实阅读需求与人工样例仍待补充。

## 开发路线

| 阶段 | 主要成果 |
| --- | --- |
| P0 需求与样例 | 产品说明、规格和评分、3 篇论文与 15 题人工证据、交互与数据流、首轮任务 |
| P1 文档链路 | PDF 上传、正文与页码解析、持久保存和查看 |
| P2 证据问答 | 固定检索、结构化回答、证据核对与开发评测 |
| P3 受控 Agent | 按缺口补检索、限步和超时、与固定流程比较 |
| P4 多篇比较与恢复 | 2—5 篇比较、持久任务、重试、取消与恢复 |
| P5 试用与求职交付 | 身份与数据隔离、部署、独立展示评测、试用反馈和演示 |

已落实 Python / FastAPI、PostgreSQL、pypdf、TypeScript / React 与 PDF.js 的本地链路。pgvector、模型、预算与部署环境待对应任务确定。原文件来源与物理页码会继续用于后续证据问答。

GitHub 保存规格、设计、代码、PR 和验证记录；Linear 维护任务状态、依赖与验收证据。更新时间：2026-09-05。
