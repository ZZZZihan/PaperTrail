# PaperTrail

AI Native 论文研读 Agent：辅助论文理解、基于原文证据的问答与阅读笔记，作为 Agent 全栈开发学习与求职实践项目。

**状态核验：2026-09-17。实现已推进到 P1/P2 的单篇论文证据问答与研读卡，但仍在未合并的开发分支；质量、人工产品与学习验收尚未完成。** 核验时 `main` 仍为原始规划文档，不含应用代码。这不等于项目尚未实现，也不等于开发分支已获批准或发布。

## 当前代码在哪里

| 入口 | 核验时提交与关系 | 状态与用途 |
| --- | --- | --- |
| `main` | `f967bcf534845c14b0d613abcce6b9e0ff482d5f` | 原始 3 次提交、6 个 Markdown 文件；未集成应用或 CI |
| [PR #1 / COL-18](https://github.com/ZZZZihan/PaperTrail/pull/1) | `codex/col-18-evidence-qa-v01` → `main`；`f0a63ed1e6856657946ae9dae7b45fbd8607db58` | Open / Draft；17 commits；PDF 导入、单篇证据问答及本地验证 |
| [PR #2 / COL-19](https://github.com/ZZZZihan/PaperTrail/pull/2) | `codex/col-19-paper-introduction-demo` → PR #1 分支；`c397ddf205266a5f6376597e36f22f7160e34a81` | Open / Draft；相对 PR #1 增加 56 commits；研读卡、回答完整性及不足状态修复 |

两项 PR 核验时均无已提交的 GitHub review、未合并。PR #2 是堆叠 PR，不是直接面向 `main` 的独立改动。**未经用户明确批准，不合并、启用自动合并或改动 PR 基线。** 详细证据、验收边界和后续集成顺序见 [交接状态](docs/handoff.md)。

PR #2 当前远端实现的本地运行说明在 [该提交的 local-development.md](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/local-development.md)；`main` 和本次文档分支不能直接执行应用的 `make` 命令。[人工试读清单](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/manual-trial-v02.md)应优先使用已有本地结果；仓库不附带原机器的数据库、PDF 库、凭据或额度。

[COL-20](https://linear.app/colife/issue/COL-20) 另有 2026-09-08 本地重构与真实验证记录；被测 `1eb2067` 和文档 `a944d86` 不属于上述已核实的远端 PR 交付。不得将其测试数或成绩归到 `c397ddf`；详见交接中的证据分层。

## 目标与第一版

围绕本人阅读 LLM / Agent 论文的真实需求，做出能使用、能核对、能评测、能部署的应用，同时掌握并解释全栈开发的核心实现。

第一版用户任务：上传一篇可提取正文的 PDF → 提出问题 → 获得答案、原文证据与 PDF 页码 → 核对引用；证据不足和处理失败均有明确状态。开发分支还提供论文研读卡、部分回答与缺失要点。先完成固定检索问答，再按实际收益引入受控 Agent，之后扩展 2—5 篇论文比较。

PaperTrail 是独立的产品与求职项目；学术选题、开题和发表研究由 mypaper 承载，RepoPilot 也独立维护。

## Codex 从这里接手

1. 阅读 [AGENTS.md](AGENTS.md) 和 [交接状态](docs/handoff.md)，先区分 `main`、PR 候选、本地未推送成果及历史记录。
2. 阅读 [项目与学习基线](docs/project-baseline.md)、[开发流程](docs/development-workflow.md)。其中 2026-09-05 的“当前阶段/未实现”文字是历史规划快照，不覆盖本次核验。
3. 连接 [Linear 项目](https://linear.app/colife/project/papertrail-论文研读-agent-93a0ba2ee811)，核对当前任务和依赖；[任务快照](docs/linear-snapshot.md)仅作历史参考。
4. 当前重点是 [COL-19](https://linear.app/colife/issue/COL-19) 的质量与人工验收，以及 [COL-20](https://linear.app/colife/issue/COL-20) 本地成果的交接核对。先保留工作区和未推送提交、确定本次候选 SHA，再继续已授权的工作；不要重新初始化已有应用，也不要自动将 P0 人工任务标为完成。

## 开发路线（目标，不是完成声明）

| 阶段 | 主要成果 |
| --- | --- |
| P0 需求与样例 | 产品说明、规格和评分、3 篇论文与 15 题人工证据、交互与数据流、首轮任务 |
| P1 文档链路 | PDF 上传、正文与页码解析、持久保存和查看 |
| P2 证据问答 | 固定检索、结构化回答、证据核对与开发评测 |
| P3 受控 Agent | 按缺口补检索、限步和超时、与固定流程比较 |
| P4 多篇比较与恢复 | 2—5 篇比较、持久任务、重试、取消与恢复 |
| P5 试用与求职交付 | 身份与数据隔离、部署、独立展示评测、试用反馈和演示 |

开发分支已使用 Python / FastAPI、PostgreSQL、pypdf、TypeScript / React、PDF.js 及固定 BM25 检索；受控 Agent、向量检索、多篇比较和公网部署不因本次状态同步而视为已完成。具体依赖、配置与运行条件以所选提交的锁文件和运行说明为准。

GitHub 保存规格、设计、代码、PR 和验证记录；Linear 维护任务状态、依赖与验收证据。状态核验日期：2026-09-17；使用前重新核对实时 refs、review、CI 与任务记录。
