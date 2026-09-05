# PaperTrail

AI Native 论文研读 Agent：辅助论文理解、基于原文证据的问答与阅读笔记，作为 Agent 全栈开发学习与求职实践项目。

**当前迭代：单篇论文研读质量 v0.2，仍在开发验证。** 研读卡解释研究问题、术语、方法输入与输出、实验依据和必要条件，区分论文陈述、作者解释、教学示意和系统推断。证据问答分别检查事实支持与要点覆盖；有依据但不完整时保留部分回答，并显示缺失项。行为与验收见 [本轮质量规格](docs/reading-quality-v02.md) 和 [研读卡规格](docs/paper-introduction-demo.md)。

本地入口：[PaperTrail](http://127.0.0.1:8000/)，也可直接打开 [ReAct 阅读页](http://127.0.0.1:8000/#paper=acffff0b-0513-4b00-98d3-40567542eda9&page=0)。优先阅读已保存的简介与问答，查看和刷新不调用模型。旧回答不会被补写覆盖成绩；旧简介可以主动“补全为研读卡”，会调用模型，升级中或失败时仍可阅读原简介。当前任务 [COL-19](https://linear.app/colife/issue/COL-19) 为 In Progress，PR 仍为 draft、未合并，产品与学习认可待用户试读。

原 react-03 已按冻结预期完成第二次真实回归，独立核对完整回答 1/1、必要条件 2/2、原子事实 16/16 有据；首次失败保留。ReAct v6 卡成功生成并保存，但独立核对发现结果段缺少本项少样本设置引文，7/8 输出单元完整支持，不能判为全部通过。v7 正在补强并列任务条件与本项引用的对应检查，并支持主动更新过时核对版本。 核心 `248a50d04d7ffa00ad75eab11de366d786510f72` 的 `make check` / 310 项后端测试通过；后续代码须有自己的检查。各版实际运行、独立评分、浏览器/重启证据及用量统一见 [v0.2 验证记录](docs/verification-quality-v02.md)。

旧成绩保留为历史基线：简介 v4 的三篇真实开发样例中，一篇可展示、两篇因结果段引用不齐而提示不足，ReAct 术语易懂程度为部分满足；见 [旧简介结果](evals/introduction-v0.1/results-2026-09-05.json) 和 [旧简介验证](docs/verification-col-19.md)。问答 v3 核心 `314d9f182f90cf6e77a32c18340172091b4ee62e` 返回 10 个回答、5 个不足提示，独立 AI 核对为 9/10 可答题完整、1 题部分覆盖，5/5 不足提示恰当，17 处引用程序核对通过；见 [旧问答结果](evals/development-v0.1/results-2026-09-05.json) 和 [COL-18 验证](docs/verification-col-18.md)。新结果不会改写这些失败或评分。

[简短人工试读与反馈](docs/manual-trial-v02.md) 优先使用已保存结果。AI 准备样例、模型自检、独立评测、人工原文核对、产品认可和学习掌握分别记录；P0 的真实需求与人工样例继续 pending。4 篇新论文、20 道候选留出题的来源与规则已冻结，完整模型评测尚未执行，不能把准备完成写成评测通过。自动找论文、Tag/分类、多篇比较、公网部署与 PR 合并不属于本轮。

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

`/health` 仅表示存活。模型配置状态可在论文问答区查看；密钥和服务配置只保存在忽略的 `.env.local`。本轮已获准使用现有中转额度，采用 `provider_quota` 模式和持久累计 167 次调用上限（用户由原 160 上限新增授权最多 7 次代表性测试）；重启不重置额度。所选模型费率未知，费用记录为未知，不能套用其他模型价格。其他机器可按已获准金额与真实单价选择 `priced`，或按已获准供应商额度与调用上限选择 `provider_quota`，详见 [本地开发说明](docs/local-development.md) 与 [人工试用准备](docs/manual-trial-v02.md)。缺少有效配置时仍可上传和阅读 PDF、查看历史，不生成模拟答案。

原导入证据见 [COL-16 验证记录](docs/verification-col-16.md)；它不代表问答或模型质量已经通过。

## 目标与第一版

围绕本人阅读 LLM / Agent 论文的真实需求，做出能使用、能核对、能评测、能部署的应用，同时掌握并解释全栈开发的核心实现。

第一版用户任务：上传一篇可提取正文的 PDF → 查看简介、理解术语与问题 → 提出问题 → 获得完整或部分回答、缺失要点、原文证据与 PDF 页码 → 核对事实及条件；证据不足和处理失败均有明确状态。先完成固定检索问答，再按实际收益引入受控 Agent，之后扩展 2—5 篇论文比较。

PaperTrail 是独立的产品与求职项目；学术选题、开题和发表研究由 mypaper 承载，RepoPilot 也独立维护。

## Codex 从这里接手

1. 阅读 [AGENTS.md](AGENTS.md) 和 [交接状态](docs/handoff.md)。
2. 阅读 [项目与学习基线](docs/project-baseline.md)、[开发流程](docs/development-workflow.md)。
3. 连接 [Linear 项目](https://linear.app/colife/project/papertrail-论文研读-agent-93a0ba2ee811)，核对当前任务和依赖；[任务快照](docs/linear-snapshot.md)仅作交接参考。
4. 当前按 [v0.2 质量规格](docs/reading-quality-v02.md) 与 [验证记录](docs/verification-quality-v02.md) 继续 COL-19；首个功能关联 [COL-16](https://linear.app/colife/issue/COL-16)。用户提出的自动查找、下载论文已记录 [COL-17](https://linear.app/colife/issue/COL-17)，尚未实现。[COL-10](https://linear.app/colife/issue/COL-10)真实阅读需求与人工样例仍待补充。

## 开发路线

| 阶段 | 主要成果 |
| --- | --- |
| P0 需求与样例 | 产品说明、规格和评分、3 篇论文与 15 题人工证据、交互与数据流、首轮任务 |
| P1 文档链路 | PDF 上传、正文与页码解析、持久保存和查看 |
| P2 证据问答 | 固定检索、结构化回答、证据核对与开发评测 |
| P3 受控 Agent | 按缺口补检索、限步和超时、与固定流程比较 |
| P4 多篇比较与恢复 | 2—5 篇比较、持久任务、重试、取消与恢复 |
| P5 试用与求职交付 | 身份与数据隔离、部署、独立展示评测、试用反馈和演示 |

已落实 Python / FastAPI、PostgreSQL、pypdf、TypeScript / React 与 PDF.js。v0.1 采用页内分块、查询转换和 BM25 固定检索；模型为可配置的 OpenAI 兼容接口。本集 16 个证据定位片段的命中数从原中文问题的 10 个增至查询转换后的 15 个；本轮没有做向量检索对比，不能据此认定 BM25 更优。向量检索与公网部署留待后续。原文件来源与物理页码用于引用归属校验。

GitHub 保存规格、设计、代码、PR 和验证记录；Linear 维护任务状态、依赖与验收证据。更新时间：2026-09-05。
