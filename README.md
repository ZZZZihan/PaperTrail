# PaperTrail

AI Native 论文研读 Agent：辅助论文理解、基于原文证据的问答与阅读笔记，作为 Agent 全栈开发学习与求职实践项目。

**当前阶段：P0 待补充，已开始本地开发基础。** 已有 FastAPI 服务入口、依赖锁定、本地检查与 CI 配置。论文样本后续选择 Agent 相关论文；PDF 上传、持久化、问答、人工评测与部署仍待实现。

## 本地启动

需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Make；本次验证使用 uv 0.10.0、Python 3.12.12。uv 会依据 `.python-version` 选择或安装 Python。

```bash
make setup
make check
make dev
```

`make setup` 按 `uv.lock` 安装依赖，并在不存在时从 `.env.example` 创建 `.env.local`。默认监听 `127.0.0.1:8000`，可在 `.env.local` 修改端口。

- 存活检查：<http://127.0.0.1:8000/health>，返回 `{"status":"ok"}`。
- 开发接口文档：<http://127.0.0.1:8000/docs>。
- OpenAPI：<http://127.0.0.1:8000/openapi.json>。

这些入口仅证明服务骨架可运行。当前不需要数据库或模型密钥。范围、版本和验证记录见 [本地开发基线](docs/local-development.md)。

## 目标与第一版

围绕本人阅读 LLM / Agent 论文的真实需求，做出能使用、能核对、能评测、能部署的应用，同时掌握并解释全栈开发的核心实现。

第一版用户任务：上传一篇可提取正文的 PDF → 提出问题 → 获得答案、原文证据与 PDF 页码 → 核对引用；证据不足和处理失败均有明确状态。先完成固定检索问答，再按实际收益引入受控 Agent，之后扩展 2—5 篇论文比较。

PaperTrail 是独立的产品与求职项目；学术选题、开题和发表研究由 mypaper 承载，RepoPilot 也独立维护。

## Codex 从这里接手

1. 阅读 [AGENTS.md](AGENTS.md) 和 [交接状态](docs/handoff.md)。
2. 阅读 [项目与学习基线](docs/project-baseline.md)、[开发流程](docs/development-workflow.md)。
3. 连接 [Linear 项目](https://linear.app/colife/project/papertrail-论文研读-agent-93a0ba2ee811)，核对当前任务和依赖；[任务快照](docs/linear-snapshot.md)仅作交接参考。
4. 2026-09-05 用户要求开始开发并后续选文，当前工程准备关联 [COL-15](https://linear.app/colife/issue/COL-15)；[COL-10：核实真实阅读需求](https://linear.app/colife/issue/COL-10/用一次真实阅读经历核实首版产品需求)仍待补充。[产品说明草稿](docs/product-brief.md)记录已知范围和缺口。

## 开发路线

| 阶段 | 主要成果 |
| --- | --- |
| P0 需求与样例 | 产品说明、规格和评分、3 篇论文与 15 题人工证据、交互与数据流、首轮任务 |
| P1 文档链路 | PDF 上传、正文与页码解析、持久保存和查看 |
| P2 证据问答 | 固定检索、结构化回答、证据核对与开发评测 |
| P3 受控 Agent | 按缺口补检索、限步和超时、与固定流程比较 |
| P4 多篇比较与恢复 | 2—5 篇比较、持久任务、重试、取消与恢复 |
| P5 试用与求职交付 | 身份与数据隔离、部署、独立展示评测、试用反馈和演示 |

已落实 Python / FastAPI 的本地运行基础。PostgreSQL、pgvector 和 TypeScript / React 仍按功能需要引入；模型、预算与部署环境待确定。详细引入条件见项目基线。

GitHub 保存规格、设计、代码、PR 和验证记录；Linear 维护任务状态、依赖与验收证据。更新时间：2026-09-05。
