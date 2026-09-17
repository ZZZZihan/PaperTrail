# PaperTrail：Codex 开发交接

状态核验日期：**2026-09-17**。本次是仓库状态与交接文档核对，不是新的代码审查、应用测试、模型评测或验收。以下 SHA 是核验快照，接手时须重新读取 GitHub refs、PR、CI 和 Linear。

**结论：main 尚未集成应用；P1/P2 工程成果在两个未合并的 Draft PR；COL-19 质量及人工验收仍未完成；COL-20 另有仅本地成果需要交接。** 不再把 2026-09-05 的“仅规划、从 COL-10 开始”当作全项目实时状态。

## 1. 项目与事实来源

PaperTrail 是论文研读 Agent 产品、全栈学习与求职项目；mypaper 承载学术研究，RepoPilot 独立维护。目标仍是单篇 PDF 的原文证据问答与研读，保留后续受控 Agent、多篇比较、部署试用路线。

GitHub refs/PR 表示已经共享的代码、审查与集成状态；CI 和验证材料必须绑定具体提交；Linear 维护任务状态与验收记录；本地工作区是另一层事实，不能仅凭 Linear 描述认定源码已经推送。README/本文是有日期的导航，不替代这些来源。

[项目基线](project-baseline.md)、[开发流程](development-workflow.md)和 [Linear 快照](linear-snapshot.md)中 2026-09-05 的阶段/环境描述保留为历史规划，不是当前应用、额度或验收状态。需要历史原文时查看 [原交接 f967bcf](https://github.com/ZZZZihan/PaperTrail/blob/f967bcf534845c14b0d613abcce6b9e0ff482d5f/docs/handoff.md)。

## 2. 已核实的远端快照与 PR 依赖

| 入口 | 完整提交 | 核验结果 |
| --- | --- | --- |
| `main` | `f967bcf534845c14b0d613abcce6b9e0ff482d5f` | 原始 3 次提交、6 个 Markdown 文件；无应用代码、依赖或 CI 工作流 |
| [PR #1](https://github.com/ZZZZihan/PaperTrail/pull/1) | `f0a63ed1e6856657946ae9dae7b45fbd8607db58` | `codex/col-18-evidence-qa-v01` → `main`；17 commits / 63 changed files；Open / Draft / 未合并；0 个已提交 review |
| [PR #2](https://github.com/ZZZZihan/PaperTrail/pull/2) | `c397ddf205266a5f6376597e36f22f7160e34a81` | `codex/col-19-paper-introduction-demo` → PR #1 分支；56 commits / 89 changed files；Open / Draft / 未合并；0 个已提交 review |

提交比较确认：PR #2 相对 PR #1 ahead 56、behind 0，merge-base 就是 `f0a63ed`；不是 56 次已合入 main 的变更。PR #2 的实现 `78b03e54afef75f5f4a2b3bfcc5e394a4502a6cf` 到 `c397ddf` 只有一个文档提交，5 个 Markdown 文件变化。

依赖关系：`main` ← PR #1（COL-18）← PR #2（COL-19）。箭头表示 PR 的目标分支，不表示已合并。核验时远端只有上述三个分支，未见 `codex/api-contract-separation`；两项 PR 的 GitHub 讨论及已提交 review 列表为空。文档中的“审查验证”或独立 AI 核对不等于 GitHub 正式 review/approval。

## 3. 验证证据分层：不要混用 SHA 或门禁

| 证据层 | 绑定版本与入口 | 能说明什么 / 不能说明什么 |
| --- | --- | --- |
| PR #1 当前 CI | `f0a63ed`；[PR run 33949547181](https://github.com/ZZZZihan/PaperTrail/actions/runs/33949547181) | 本次查询状态为 completed / success；不等于人工认可、合并或发布 |
| PR #2 当前 CI | `c397ddf`；[PR run 34175932728](https://github.com/ZZZZihan/PaperTrail/actions/runs/34175932728) | 本次查询状态为 completed / success；未在本次审计重跑，也不是实时模型质量结论 |
| PR #2 最近离线实现验证 | `78b03e5`；[2026-09-07 记录](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/verification-requested-facts-2026-09-07.md) | 记录为 make check、407 后端测试、16 项 API E2E、7 组浏览器断言通过；使用离线 mock，真实调用 0；E2E/浏览器不是上述 CI 成绩 |
| 远端保留的历史真实结果 | [v0.2 质量记录](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/verification-quality-v02.md)、[2026-09-06 真实 E2E](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/verification-live-e2e-2026-09-06.md) | 旧留出严格完整 4/16、超时 3/20；9 月 6 日精确不足案例 0/1。它们是旧版本结果，不是 QA v8 已完成真实回归 |
| 后续仅本地结果 | [COL-19](https://linear.app/colife/issue/COL-19)、[COL-20](https://linear.app/colife/issue/COL-20) 的 2026-09-08 记录 | 被测 `1eb2067`，文档 `a944d86`；质量 FAIL：开发严格完整 5/10、原留出 6/16、原留出工程失败 5/20。来源是 Linear，本次未取得本地源码/原始产物，不归到远端 `c397ddf`，不称独立复现 |

远端文档中的“本地提交、未推送”反映 9 月 7 日撰写时状态；截至本次核验，该实现和文档已在 PR #2 的 `c397ddf` 中，且有上述远端 CI。纠正的是交付位置，不将离线成绩提升为真实模型成绩。旧额度、用量及“最新实测”等也须按各自日期理解，不当作当前运行环境测量。

COL-20 在 Linear 报告的分支为 `codex/api-contract-separation`：实现 `5769e1ed290da9f3003361c5768ce401b82f223c`，工程交付/后续被测 `1eb2067d20898cca964c76cebbaaba11e0850afb`，真实验证文档 `a944d8604929611f48001a6d0551226f72c602cc`。其 543 项后端测试、接口契约与双前端入口验证同样是本地报告，不属于 PR #2 当前远端 CI。该本地分支与远端候选的完整祖先关系尚待取得本地 Git 后核对，不能假定直接叠在 `c397ddf` 上。

## 4. 任务状态、验收与发布分别记录

本次实时读取 [PaperTrail Linear 项目](https://linear.app/colife/project/papertrail-论文研读-agent-93a0ba2ee811)：COL-18 为 Done；COL-15、COL-16、COL-19、COL-20 为 In Review；COL-10 为 Todo；COL-11—COL-14 与 COL-17 为 Backlog。这里只记录快照，不复制任务正文或变更其状态。**COL-18 Done 不表示 PR #1 已合并。**

| 门禁 | 当前结论 |
| --- | --- |
| 已共享的工程实现 | PR #1/#2 存在应用、规格和验证材料；尚未集成 main |
| 确定性工程检查 | 上述具体提交有成功 CI / 离线记录；不覆盖未推送版本或未来变更 |
| 真实模型质量 | 远端 QA v8 的新规则未形成真实通过证据；后续本地版本在 Linear 明确报告 FAIL |
| GitHub review / approval | 两项 PR 均无已提交 review；本次审计不代替代码审查或批准 |
| P0 人工需求/样例、人工原文核对、产品认可、学习验收 | 仍有待完成项；[人工试读模板](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/manual-trial-v02.md)不能当作实际反馈 |
| 合并与发布 | PR #1/#2 未合并；不能以 CI、任务 Done 或本文声称应用已集成或已发布 |

## 5. 下一次接手的最小顺序

1. **保留并核对版本。** 先查看工作区、当前分支和未推送提交，再读取远端与 Linear。不要 reset/clean、覆盖本地配置、删除数据库/PDF 库，或为“同步 main”丢弃 COL-20 工作。未推送源码不在本次审计可访问范围内。
2. **确定这次候选与证据。** 审阅远端候选时用 `c397ddf`；继续 COL-20 时先在原环境确认上述本地 SHA、祖先关系和工作区差异，再在授权范围内准备独立交付。不得从 Linear 文字重新生成一个冒充原提交的实现，也不要把后续本地成绩抄成 PR #2 的通过记录。
3. **先处理验收缺口，不扩功能。** COL-19 优先不足输出状态冲突、必要条件遗漏、本项引文支持及超时；以上后一轮失败来自本地被测版本，修复前先在选定候选上复现。若使用原留出调参，先转开发集并冻结新的未见留出。不要借状态清理扩展自动找论文、Tag、多篇、Agent 或公网部署。
4. **人工试读与学习单独留证。** 用已保存结果记录用户实际认可、遗漏、不满意及原文核对；记录其实际复述。没有反馈就保持 pending，不代填、不要求为了填表反复调用模型。
5. **准备审阅，不擅自集成。** 先按依赖审阅 PR #1，再审阅 PR #2 的增量。是否拆分、更换候选或集成由用户决定；批准前不合并、不启用自动合并、不改基线或关闭现有 PR。未来若批准集成，先处理 PR #1，再根据实际合并方式重新核对 PR #2 的 base、diff 和 CI，不能假设 squash 后依赖自动保持正确。

不改动工作区的版本核对示例（在本地仓库执行；本次审计未运行这些命令）：

```bash
git status --short --branch
git log -5 --oneline
git fetch origin
git rev-parse origin/main
git rev-parse origin/codex/col-18-evidence-qa-v01
git rev-parse origin/codex/col-19-paper-introduction-demo
```

这些操作不切换或覆盖工作区；`git fetch` 会更新远端跟踪引用。需要运行应用时，先选择并确认实现分支，然后读取其 [local-development.md](https://github.com/ZZZZihan/PaperTrail/blob/c397ddf205266a5f6376597e36f22f7160e34a81/docs/local-development.md)；不要在 main/本次文档分支执行不存在的 `make setup` 或 `make serve`。原机器的缓存和数据不会因 clone 自动出现。任何真实模型调用都须依据当前任务授权和实际配置，不沿用历史临时不限量授权。

## 6. 本次文档交付边界

最小改动仅为 README.md、AGENTS.md、docs/handoff.md；不引入应用、依赖、评测数据或新的任务看板。原始规划和历史验证保留在原文件与 Git 历史，本文只给出日期、提交、来源与待办。

文档更新单独针对 main 准备，PR #1/#2 的代码头与基线保持不动。即使将来批准这项文档更新，也不代表批准两个功能 PR；合入后仍需保留“应用尚未集成”的说明。由于两个功能分支也修改了这些入口文件，后续获准集成时必须保留本次状态分层，并结合最新分支内容处理冲突，避免旧文字再次覆盖。

本次仅核对远端 refs/提交比较、文件、PR 讨论/review、指定 SHA 的 PR CI 状态及 Linear 记录；检查文档内容、链接与改动范围。没有运行应用测试、真实模型、人工试读或部署，没有修改额度、数据或验收结论。
