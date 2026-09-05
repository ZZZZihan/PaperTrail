# PaperTrail

AI Native 论文研读 Agent：辅助论文理解、基于原文证据的问答与阅读笔记，作为 Agent 全栈开发学习与求职实践项目。

**当前阶段：单篇论文证据问答 v0.1 已进入集成与开发验证，真实模型验证待本地配置。** 在原 PDF 导入上增加中文提问、固定检索与模型生成、引用来源校验、AI 支持检查及持久问答历史。页面保留原文与逐页文字，点击引用可回到对应 PDF 物理页。未配置模型及获准预算时不会生成模拟答案。

本轮主任务 [COL-18](https://linear.app/colife/issue/COL-18)；[行为规格与数据流](docs/evidence-qa-v01.md)、[3 篇 15 题开发诊断](docs/development-diagnostics.md)、[约 25 分钟人工清单与反馈模板](docs/manual-trial-v01.md)、[本轮验证与剩余依赖](docs/verification-col-18.md)。AI 准备样例与用户人工核对分别记录，P0 真实需求、产品效果及学习验收继续待完成。

## 本地启动

需要 uv、Make、Node.js 22.13+ 与 PostgreSQL 17。macOS 可执行 `brew install postgresql@17`；数据库由项目脚本管理，不需要启用 Homebrew 登录服务。Python 版本由 `.python-version` 固定。

```bash
make setup
make check
make serve
```

`make setup` 安装锁定的 Python 与网页依赖，在不存在时复制 `.env.example` 为 `.env.local`。`make serve` 构建网页、启动专用本地 PostgreSQL（127.0.0.1:55432），再启动无自动重载的试用服务；开发时可用 `make dev`。

- **网页：<http://127.0.0.1:8000/>**，上传 PDF 后进入逐页核对。
- 数据保存于忽略目录 `data/library/` 和 `data/postgres/`；刷新或重启后保留。
- 在运行终端按 Ctrl+C 停止应用，`make db-stop` 停止项目数据库；下次 `make serve` 继续使用原有论文和问答历史。

- 存活检查：<http://127.0.0.1:8000/health>，返回 `{"status":"ok"}`。
- 开发接口文档：<http://127.0.0.1:8000/docs>。
- OpenAPI：<http://127.0.0.1:8000/openapi.json>。

`/health` 仅表示存活。模型配置状态可在论文问答区查看；密钥、模型地址/名称、预算与币种及每百万 token 单价仅配置于忽略的 `.env.local`，详见 [本地开发说明](docs/local-development.md)。没有模型配置时仍可上传和阅读 PDF、查看历史。

原导入证据见 [COL-16 验证记录](docs/verification-col-16.md)；它不代表问答或模型质量已经通过。

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

已落实 Python / FastAPI、PostgreSQL、pypdf、TypeScript / React 与 PDF.js。v0.1 采用页内分块、查询转换和 BM25 固定检索；模型为可配置的 OpenAI 兼容接口。向量检索需依据开发诊断收益再决定，公网部署留待后续。原文件来源与物理页码用于引用归属校验。

GitHub 保存规格、设计、代码、PR 和验证记录；Linear 维护任务状态、依赖与验收证据。更新时间：2026-09-05。
