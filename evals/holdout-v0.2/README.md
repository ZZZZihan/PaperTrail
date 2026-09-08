# 单篇论文证据问答候选留出集 v0.2

本集关联 [COL-19](https://linear.app/colife/issue/COL-19) 后续准确性与专业度 goal。状态是 **AI 准备并核对来源的 candidate_holdout**：冻结前零应用问答运行、零评测模型调用、未用于调试；所有人工核对仍为 pending。这是准备成果，不是已取得的模型成绩，也不替代用户真实阅读样例。

实现与调参人员应只读本说明和校验摘要。题目与答案键交给独立评测环节；准备标注的 AI 已接触原文及答案，不属于盲审。按论文隔离并不能排除模型预训练曾见过公开论文。

## 固定范围

| 论文 | 固定来源 | 物理页数 | 每篇题数 |
| --- | --- | ---: | ---: |
| Chain-of-Thought Prompting | [arXiv 2201.11903v6](https://arxiv.org/abs/2201.11903v6) | 43 | 5 |
| Self-Consistency | [arXiv 2203.11171v4](https://arxiv.org/abs/2203.11171v4) | 24 | 5 |
| Retrieval-Augmented Generation | [arXiv 2005.11401v4](https://arxiv.org/abs/2005.11401v4) | 19 | 5 |
| Dense Passage Retrieval | [EMNLP 2020 出版 PDF](https://aclanthology.org/2020.emnlp-main.550/) | 13 | 5 |

四篇均与 [development-v0.1](../development-v0.1/questions.json) 的 ReAct、Reflexion、Toolformer 分离。每篇恰好覆盖直接事实、跨段/跨章节条件、错误前提反证、证据不足、表格与脚注/表注探测各一题。合计 **16 道可答题、4 道不足题、62 个答案评分点、29 个必要条件、46 个原文上下文引用**。四道表格题仍在可答分母中，单独分组报告其能力边界。

`questions.json` 保留原诊断集的基本键，同时增加具有稳定 ID 的 `answer_points`、`required_conditions` 与 `evidence_ids`。不足题的可答要点和必要条件列表为空，已知近似信息、检查范围、缺失字段与禁止推断写在 `insufficient_reason` 和 `insufficiency_audit` 中，不混进可答题条件分母。

## 原文与冻结

- `manifest.json` 固定原始 PDF URL、版本、字节数、SHA-256、页数、逐页提取文字 SHA-256，以及 `pypdf 6.17.0` 提取方式。arXiv URL 带明确版本；ACL 的出版 PDF 以完整 SHA-256 固定本次取得的版本，若出版方以后替换文件必须报错。
- 原始 PDF 保存在被 Git 忽略的 `data/diagnostics/holdout-v0.2/sources/`，初始发布为 `0444`。它们没有进入应用论文库或修改数据库。
- `evidence.json` 固定完整上下文的物理页索引、字符起止及 SHA-256，只附很短的精确定位引文。完整上下文从原 PDF 按偏移重建，避免在 Git 中复制长段论文正文。物理页索引从 0 开始，界面页号为索引加 1；DPR 另有期刊印刷页码，不得混用。
- `checksums.sha256` 覆盖 `manifest.json`、`questions.json`、`evidence.json` 与 `rubric.json`。校验器不生成或修改这些哈希。哈希提供漂移检测，不是独立签名，也不是语义正确证明。
- 标注 AI 亲读相关章节与证据上下文。表格人工视觉操作由 AI 执行：CoT PDF 第 23 页、Self-Consistency 第 4/5 页、RAG 第 6 页、DPR 第 5/6 页。核对表头、行名和表注/脚注；这不等于用户人工验收。RAG 未采用原文与某人评表存在数字冲突的案例。

## 重复校验

在仓库根目录执行：

```bash
uv run python scripts/fetch_holdout_papers.py --verify-only
```

默认也是只读离线。它校验四份冻结 JSON、四个 PDF、逐页文字、46 处上下文与定位偏移、同篇证据归属、每篇题型数、人工 pending 标记、原子要点和条件分母，以及不足题已冻结的关键词命中页。命令不调用模型、不接触应用数据、不写文件；源文件缺失、版本漂移或提取器不同都会失败。

若在另一台机器缺少源 PDF，可显式执行：

```bash
uv run python scripts/fetch_holdout_papers.py --fetch-missing
```

只允许已冻结的四个 HTTPS 来源。下载先在内存校验完整哈希与逐页提取，再以排他方式发布；已有文件不会覆盖，重定向不会被接受。下载错误或出版版本变化需要核对原始来源，不能放宽哈希使其通过。

独立来源审查者可把完整上下文导出到本机忽略目录；不要把此内容交给正在调整实现的代理：

```bash
mkdir -p output/holdout-review
uv run python scripts/fetch_holdout_papers.py --verify-only --context-report > output/holdout-review/contexts.json
```

## 评分与使用边界

冻结规则见 `rubric.json`。完整有据回答、已输出事实支持、必要条件覆盖、实际检索充分性、误拒答、无据作答、工程失败及用量分别报告。答案中已有事实有据不代表覆盖完整；只藏在引文里的必要条件不算已解释。等价的同篇证据可以支持答案，不要求机械命中预期短语。

后续运行前，先从同一已授权 `provider_quota` 持久账本读取实际余量，再记录运行选择、源码/模型/提示版本与数据集哈希。本集的准备不增加调用授权；候选留出题本轮尚未执行，不能填写全量质量分数。若仅执行子集，明确记录选中数/总数和未运行题目；工程失败保留在对应已选择分母内。

每次实际使用应在独立不可覆盖的运行目录中增加使用事件，记录来源数据集哈希、论文 ID、用途、时间和对应代码版本；冻结文件本身保留初始事实。一旦任何题目、标签或失败反馈用于修改实现，该论文全部题目失去留出资格，后续展示前需另准备新论文替换并独立冻结。人工纠正标签也要新版本与变更理由，不能直接改旧答案键并重算校验值。

## 本次验证

实际执行：`uv run python scripts/fetch_holdout_papers.py --verify-only` 通过上述来源及结构检查；`uv run pytest -q tests/test_holdout_sources.py` 为 17 passed；新脚本及测试的 Ruff 格式和静态检查通过。测试覆盖来源被改、校验目录缺项/重复/越界、离线缺件不联网不写目录、下载验证后才发布、已有文件和并发发布不覆盖、提取漂移、固定来源 URL、符号链接以及人工标记漂移。

这三类证据分别证明来源一致性、确定性脚本行为与代码规范，不证明模型能正确作答。根任务负责汇总本轮 `make check` 与运行应用证据；本子任务没有运行应用问答，也没有修改 COL-18 的冻结成绩或调用账本。
