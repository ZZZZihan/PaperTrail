# COL-18：单篇证据问答 v0.1 开发验证

日期：2026-09-05。任务 [COL-18](https://linear.app/colife/issue/COL-18)，分支 `codex/col-18-evidence-qa-v01`，[Draft PR #1](https://github.com/ZZZZihan/PaperTrail/pull/1)。

**结论：工程实现与开发验证完成，可供本地试用。用户产品效果、学习验收与原 P0 人工样例/评审均待核对。** 已完成真实论文、真实 Sol 调用、15 题诊断及独立 AI 核对、浏览器上传/提问/引用/历史/刷新/应用和数据库重启。该结论不代表所有新问题都能正确回答，也不代表合并或公网发布。

核心实现和最终真实诊断绑定完整 SHA `314d9f182f90cf6e77a32c18340172091b4ee62e`，诊断开始时工作区干净；随后仅补充交付文档及脱敏结果汇总。交付文档版本使用 `git rev-parse HEAD`，最终 CI 见 PR Checks；不能把旧 SHA 的通过当作新提交结果。

## 可复现启动与数据流

本机试用入口：<http://127.0.0.1:8000/>，已配置 `gpt-5.6-sol`。仓库根目录执行 `make serve`；停止时 Ctrl+C，再 `make db-stop`；再次 `make serve` 使用原数据。保留 `data/library/` 与 `data/postgres/`，不要以删除数据的方式重启。首次安装、兼容服务请求参数及两种预算模式见 [本地运行](local-development.md)，人工试用见 [约 25 分钟清单与反馈模板](manual-trial-v01.md)。

固定路径：PDF 原件及逐页文本 → 当前论文、不跨页分块 → 一次中文问题查询转换 → BM25 检索 → 一次结构化事实生成 → 程序核对引用存在和论文归属 → 一次 AI 语义支持检查 → PostgreSQL 保存终态与调用记录。每题独立，最多 3 次模型调用，不自动重试。引用按 PDF 文件物理页序定位，前端可查看 PDF 和提取文字；引用位置合法与语义支持分别判定。

沿用 FastAPI、uv、PostgreSQL 17、pypdf、React/TypeScript、PDF.js。新增 SQL 表不清空原论文库。规格与取舍见 [行为及数据流](evidence-qa-v01.md)，核心入口是 `src/papertrail/qa.py`、`retrieval.py`、`model.py`、`budget.py`、`questions.py` 和 `web/src/QuestionPanel.tsx`。

## 最终真实模型与 15 题诊断

用户提供 OpenAI 兼容本地中转站并授权使用其现有额度，随后选择 luna / sol / Astra 系列；最终采用服务目录列出的 **gpt-5.6-sol**，返回 model 字段也为该名称。这里只记录服务报告的模型标识，不独立保证权重或模型快照。凭据只保存在被忽略、权限 0600 的 `.env.local`，不进入仓库或报告。

最终配置：`profile=openai`，`max_completion_tokens=4096`，`reasoning_effort=none`，JSON object 输出，单调用超时 45 秒，不发送 temperature/thinking；pipeline/prompt 为 `evidence-qa-v3`。检索采用每页 1400 字符、200 重叠，BM25 k1=1.5 / b=0.75，top 12、最多 20,000 字符。逐次提示版本与哈希、参数、请求/输出哈希和用量在原始 trace 中；提示模板见该 SHA 的 `src/papertrail/qa.py`。trace 不保存完整请求 messages，不能把哈希当作提示正文。

来源冻结为 [ReAct v3](https://arxiv.org/abs/2210.03629v3)、[Reflexion v4](https://arxiv.org/abs/2303.11366v4)、[Toolformer v1](https://arxiv.org/abs/2302.04761v1)，共 69 页，15 题（10 可答、5 不足）、16 个参考定位片段。PDF SHA、来源检查、题目及预期保持固定。全部是 **AI 准备、待人工核对的开发样例**，不是用户阅读记录。详见 [诊断集与评分](development-diagnostics.md)。

最终 run：`data/diagnostics/runs/20260905T060149.314665Z-cda725c451624134b44e29bcd1251694/`。运行命令为 `uv run --locked python scripts/evaluate_development.py --run`；运行器经应用 API 调用真实模型，预期答案不传入生成。每轮另建不可覆盖目录；原结果、失败、重跑均保留。

| 检查 | 最终结果 |
| --- | --- |
| 应用终态 | 10 answered、5 insufficient_evidence、0 failed，15/15 历史回读一致 |
| 程序引用检查 | 返回的 17 个引用全部存在、页码及当前论文归属正确，片段身份可复核 |
| 独立 AI 语义检查 | 15 条实际事实均有引用支持；10 道可答题中 9 完整通过、1 部分覆盖；5/5 不足题恰当拒答 |
| 未完整题 | ReAct-03：PaLM-540B、HotpotQA 6 例/FEVER 3 例正确，但回答未写冻结模型及人工编排轨迹条件；引用中的信息不自动补为正文事实 |
| 开发门槛 | ≥8/10 可答题完整、5/5 不足、无伪造引用，满足；用户人工评分全部 pending |
| 耗时 | 逐题墙钟中位数 15.045 秒、P95 26.197 秒，15 题墙钟合计 234.982 秒；应用处理合计 230.249 秒 |
| 用量 | 40 次调用，93,521 输入 + 4,449 输出 = 97,970 tokens；40 次费用均未知 |
| 原中文 / 查询转换检索 | 10 可答题的 16 个参考片段命中 10/16 → 15/16；不足题无参考锚点，记 N/A |

P95 使用 `(n−1)×0.95` 位置线性插值。检索统计只是冻结定位片段覆盖率，不等于语义评分；未命中的是 ReAct-03 第一锚点。该结果支持 v0.1 保留查询转换加 BM25，但没有进行向量检索对照，不能宣称优于向量方案。

独立审查由 AI 另读实际输出、引用和冻结同页上下文，不把流水线的支持判定直接当评分。报告为 `output/model-gateway/review-react-v3.json`、`review-reflexion-v3.json`、`review-toolformer-v3.json`；三者分别为 4 通过/1 部分、5 通过、5 通过。原 run 中的人工/外部待评字段不回写伪造完成。可提交的脱敏逐题汇总见 [results-2026-09-05.json](../evals/development-v0.1/results-2026-09-05.json)，完整实际回答、引用、trace、模型/提示配置和逐题结果留存在上述本机原始目录。

## 失败轮次与修复证据

| 实际轮次 | SHA / 提示 | 结果 | 调用 / tokens |
| --- | --- | --- | --- |
| mini 单题烟测 | `249b676` / v1 | 1 题 invalid_citation；当时未保存生成候选，确切字符原因未知 | 2 / 6,193 |
| Sol 单题烟测 | `882a385` / v1 | 1 回答，4 引用合法且有支持 | 3 / 7,114 |
| Sol 完整 v1 | `882a385` / v1 | 7 回答、5 不足、3 invalid_citation | 37 / 93,725 |
| Sol 完整 v2 | `7714554` / v2 | 7 回答、7 不足、1 invalid_citation | 39 / 95,478 |
| Sol 完整 v3 | `314d9f1` / v3 | 10 回答、5 不足；独立评分如上 | 40 / 97,970 |

v1 的三个真实失败来自 PDF 连字符换行（如 `PaLM-` 后换行、跨行断词）与模型摘引形式不一致。引用校验现在只对原文中实际存在的 ASCII 连字符加换行做受限源跨度恢复，返回的仍是真实连续原文；不全局删空格、不做模糊匹配、不跨论文找近似句子。

v2 中一个模型附加公式改变空白，继续被拒绝；两个过度展开的回答各有一条事实依赖其他事实的引用，或带上当前引用未支持的细节，被语义守卫转为证据不足。v3 提示要求先找完整证据，再写必要的 1–3 条事实，每条限定语都由本条引用支撑，减少无关公式与机制扩展。**未降低引用规则、未移除失败题、未修改冻结预期，也未增加自动重试。** v3 的 ReAct-03 条件遗漏仍保留为已知问题。

这些题用于开发反馈后调整过提示，因此成绩属于开发诊断，不是留出集泛化成绩。五轮清单经 Python 和独立原生 `shasum -a 256 -c artifacts.sha256` 复核均通过；v3 清单 SHA 为 `935e7d87dca5466cb034edef24020176e376c2441fe511e148ecefe37ca02353`。完整统计在 `output/model-gateway/diagnostic-statistics.json`。

## 真实浏览器与完整重启

真实 Chromium 会话 `papertrail-v01`，使用端口 8000 的真实库和真实模型。早期已从浏览器上传 Reflexion、Toolformer，保留原有 ReAct；三篇原文件哈希核对一致。最终配置后再次从文件选择器上传固定 ReAct，重复导入正确打开已有记录。

随后从浏览器实际提交：

1. “在 ReAct 的 Wikipedia 搜索动作中，如果找不到指定实体，系统会返回什么？”——ID `43957f8b-6172-4671-a44f-4a7e077a6ecf`，3 次 Sol 调用，回答“前 5 个相似实体建议”，原文片段直接支持。点击引用从 PDF 第 2 页跳到第 4 页，原 PDF、页码、证据定位条正确显示。
2. “每个 WebShop 任务的模型调用费用折合人民币多少钱？”——ID `e5eebdae-5c13-4caa-b453-ce1786cfab24`，2 次 Sol 调用，返回当前已检索证据不足，说明这不代表整篇一定没有信息，并给改问入口。
3. 刷新网页，打开已保存历史。确认没有进行中的问题后，停止 Uvicorn、执行 `make db-stop`，再 `make serve`。3 篇论文、49 条真实问答的完整 JSON（含答案/引用/trace）、元数据、原 PDF 哈希及第一页文本前后完全相同；两个快照 SHA 同为 `c4489916be646d2255606f7e85a25638c5eed09d6227d1075f1a80ac578cafac`。
4. 重启后刷新浏览器，ReAct 历史仍为 19 条，第 4 页恢复；切回第 1 页，展开上面的旧问题并点击引用，再次跳到第 4 页。

真实库共 49 条：ReAct 19、Reflexion 15、Toolformer 15；包含开发早期失败记录，不清理为全绿外观。软件验证人员为 AI，用户尚未执行人工试用。

本机证据目录 `output/model-gateway/`：`browser-real-v3-page-4.png`、`browser-insufficient-real-v3.png`、`browser-history-after-restart-v3.png`、相关 `browser-*.txt`、`real-v3-before-restart.json`、`real-v3-after-restart.json`、`restart-verification-v3.json`、`serve-trial-final.log`。正常真实浏览器控制台为 0 errors / 0 warnings，结果另存 `browser-console-final.txt`。

## 确定性、故障与 CI 检查

最终代码的 `make check` 返回 exit 0：**157 tests passed**，Ruff、TypeScript/生产构建、HTTP/OpenAPI 冒烟和 diff 检查通过。日志 `output/model-gateway/make-check-prompt-v3.log`，157 项测试 4.07 秒；有一项 Starlette 对 anyio 别名的弃用提示。业务测试使用隔离临时 PostgreSQL，不清空开发库。

测试覆盖：导入失败/恢复、跨论文或伪造引用/页码、受限换行恢复与拒绝边界、AI 不支持、证据不足、模型无配置/超时/失败/非法 JSON、OpenAI 参数方案、显式 HTTP 来源、额度持久累计及未知费用、重复 request_id、全局单任务、数据库 session 丢失阻止下一次调用、历史与启动中断恢复。此前修复阅读器资源加载失败导致整页空白、重复启动误恢复其他服务任务、账本快照异常阻碍终态保存等阻断风险。

早期故障浏览器证据封存于 `7e813e0` 版本（`output/playwright/v01/verification-artifacts.json`），使用 `uv run --locked python scripts/browser_fixture.py`，独立临时数据库/合成 PDF/8001 端口，模型明确标为 `OFFLINE-UI-FIXTURE-NOT-A-REAL-MODEL`，transport 强制 MockTransport，无真实调用。它已完成正常、证据不足、超时、模型失败、无效引用五题，错误原因/继续方式可见，5 条历史刷新及应用+数据库重启前后完全一致；引用从第 1 页跳第 3 页，提取文字高亮。另注入 PDF 懒加载资源 503，阅读器局部提示和恢复入口生效，历史仍可用。证据位于 `output/playwright/v01/OFFLINE-*`。这些是可重复软件故障测试，不作为真实模型质量成绩；后续实现有变更，未声称所有故障态都在最终 SHA 再做浏览器注入。最终核心版本由上述 157 项检查和真实主流程重验覆盖。真实配置缺失界面见同目录 `real-paper-model-unconfigured.png`；真实无效引用和支持失败另有上述 v1/v2 记录。

核心 SHA `314d9f1` 的远端 push / PR CI 均 success：[push](https://github.com/ZZZZihan/PaperTrail/actions/runs/33948717221)、[PR](https://github.com/ZZZZihan/PaperTrail/actions/runs/33948718935)。首次远端在旧 SHA 的镜像下载遇到 403，已将锁文件默认源切到官方 PyPI，30 个包版本及文件哈希不变；[旧失败](https://github.com/ZZZZihan/PaperTrail/actions/runs/33946522402)保留。最终交付文档提交的 CI 另以 PR Checks 及本机 `output/model-gateway/remote-ci-delivery.json` 核对。

## 用量与剩余限制

截至 2026-09-05 14:12（北京时间），真实库账本共有 **126 次调用、312,546 tokens**（295,102 输入 / 17,444 输出），包含五轮诊断 121 次与浏览器 5 次，全部已结束。124 次 Sol 调用费用未知；mini 2 次按当时配置价格估算 USD 0.00776850。总费用和供应商余额未知，不能套用 mini 单价推算 Sol，也不能把已知费用小计当总账单。账本摘要见 `output/model-gateway/ledger-delivery.json`。

当前 `provider_quota` 模式使用用户已授权额度，持久调用上限 160 次；上述时点本地剩余 **34 个调用槽位**（不等于供应商余额，每题最多 3 次）。失败、旧模型及重启前调用均累计；刷新、换模型和重启不重置。先前 100 次开发保护值已按完整第三轮诊断及试用需要调整为 160，scope 不变，没有绕过已消耗记录。此保护是次数上限，不是美元限额。

- ReAct-03 的实验条件遗漏；检索和回答仍可能漏掉限定词，用户应逐条查看原文。语义检查与生成使用同一服务模型，仍可能共同误判。
- 开发集只有 3 篇英文 Agent 论文、15 题，并据反馈改过提示；未证明其他领域、中文论文或任意新题泛化质量。
- 证据不足提示较通用。扫描件、复杂图表、公式、抽取错序及跨页语义不在可靠覆盖范围；最多 20 MB / 100 页，不提供 OCR。
- 每题独立，不使用对话上下文；本地单用户、单进程、问题串行。处理中断后保留失败历史，由用户主动重试，不自动续费调用。
- 暂不包含自动找论文与下载、多篇比较、Agent 循环、多用户或公网部署。用户真实阅读样例、产品效果与学习验收继续单独待定。
