# 独立质量评测与离线评分 v0.2

版本：2026-09-05，关联 [COL-19](https://linear.app/colife/issue/COL-19) 及单篇研读质量改进 goal。本文定义评分数据契约与统计口径，不宣称新增模型评测已经完成。实际执行题目、回答版本与逐题审查另留证据；用户的产品认可、人工样例核对和学习掌握仍需本人反馈。

## 要解决的问题

应用内的引用归属校验、事实支持检查和覆盖检查属于产品防护，不能直接作为独立成绩。一个回答可能只写出少量有据事实，却遗漏问题要求的大半要点；也可能所有引用都能定位，但引用并不支持回答。旧 `react-03` 的冻结模型与人工编写轨迹条件继续保留，不能为提高分数放宽已有预期。

[离线评分脚本](../scripts/score_quality.py) 只创建审查表、核对输入身份、汇总已经填写的独立判断。它不联网、不调用模型、不读取凭据、不操作预算账本，不利用应用的 `support_verdicts`、`support_status` 或 `coverage` 为答案打分。用户或独立 AI 必须阅读实际输出和充分的原文上下文后填写结论；未审查的字段保留 JSON `null`。

## 先冻结资料与评分范围

继续保留原 [3 篇 15 题开发集](development-diagnostics.md)，它已参与调试；新候选留出集按论文隔离，在使用前冻结来源版本、问题、必要要点、必要条件、原文上下文和规则。候选集尚未运行时，不填写成绩；一旦用它调试，记录失去留出资格并补建新的留出材料。

评分读取 [现有运行器](../scripts/evaluate_development.py) 生成的目录布局：

```text
run/
  invocation.json                  # mode=run，git.sha，dirty 与文件版本记录
  selection.json                   # 预先选择的 question_ids，决定分母
  dataset/
    manifest.json                 # paper id、PDF SHA256、逐页文本 SHA256 等
    questions.json                # 冻结问题、预期状态与要点
    checksums.sha256              # 至少绑定 manifest/questions，可同时绑定 evidence/rubric
    evidence.json                 # 如果该数据集包含它，加入 checksums
    rubric.json                   # 如果该数据集包含它，加入 checksums
  sources/
    PAPER_ID.pdf
    PAPER_ID.pages.json
  questions/
    QUESTION_ID.result.json       # 可缺失；缺失结果仍保留在分母与 missing 列表
```

逐题 result 采用现有运行器格式：`question_id`、与冻结题完全一致的 `input`、`runner_status`、应用 `result`、可选的 `metrics`、`wall_elapsed_seconds` 和独立程序的 `deterministic_citation_check`。`runner_status=completed` 必须有终态；兼容 `answered`、`partial_answer`、`partially_answered`、`insufficient_evidence` 与 `failed`。运行器失败即使已经获得部分应用输出，也仍记为工程失败。

题目最小字段为 `id`、`paper_id`、`expected_status`、`expected_answer_points`。如果另有带稳定 ID 的 `answer_points: [{id,text,evidence_ids}]`，其文本列表必须与 `expected_answer_points` 一致。`required_conditions: [{id,text,evidence_ids}]` 独立列出本题必要条件；也兼容字符串列表。`evidence_ids` 的含义由冻结的证据文件说明，评分器不会通过命中证据 ID 自动判定语义支持。

`expected_status=answered` 也包括可由论文明确反驳的错误前提。只有当前允许的论文材料不能支持答案或反驳时才标为 `insufficient_evidence`。错误前提不应一律变成拒答题。

必要条件不存在时使用经过标注的空列表 `[]`；字段缺失代表条件判据尚未标注，不能解释成“不需要任何条件”。旧集可用单独的条件注释文件补充原冻结要点已经要求的条件，原问题文件不变：

```json
{
  "schema_version": 1,
  "questions": {
    "react-03": {
      "required_conditions": [
        {"id": "frozen-model", "text": "主提示实验的 PaLM-540B 模型保持冻结。"},
        {"id": "manual-trajectories", "text": "所选案例的推理动作轨迹由人工编写。"}
      ]
    }
  }
}
```

注释只能增加条件标注，不能覆盖题目已经冻结的 `required_conditions`，也不能修改原答案要点。它的 SHA256 进入审查绑定。新实验应在运行前冻结注释；旧结果的回溯注释要明确标为回溯分析，不假装早已预注册。哈希只能检测绑定后的字节变化，不能证明标注时间或评审独立性。

## 创建与填写独立审查表

以下 `RUN` 指向一次已经保存的实际运行目录。每次生成使用新的输出文件名，脚本拒绝覆盖已有文件。`--rubric` 为可选的条件注释；创建与汇总时必须使用同一份字节内容。

```bash
uv run --locked python scripts/score_quality.py template \
  --run "$RUN" --review-source ai \
  --rubric "$RUBRIC" --output "$NEW_AI_REVIEW"

uv run --locked python scripts/score_quality.py template \
  --run "$RUN" --review-source human \
  --rubric "$RUBRIC" --output "$NEW_HUMAN_REVIEW"
```

根对象包含 `schema_version=papertrail-independent-quality-v1`、`review_source=ai|human`、`reviewer`、`method`、`reviewed_at`、`bindings`、`items`。未填写模板的身份字段可以是 `null`；一旦出现实际判断，必须写明评审者、方法和时间。AI 表与人工表分别填写、分别汇总，不混合成一份“人工通过率”。只复制 AI 意见到人工表不能建立人工核对证据。

`bindings` 固定运行代码 SHA、dirty 标记、完整预选题 ID、输入/选择/运行记录/来源/每题结果的 SHA256，以及可选条件注释的哈希。缺失结果也以 `null` 绑定；如果它后来出现，必须基于新的实际快照创建审查表。题目 ID、答案点 ID/文本、条件 ID/文本必须与冻结数据一致，不能删掉失败题或修改预期后复用旧审查。

逐题审查项如下。这里的示例文字仅说明契约，不是实际评测结论：

```json
{
  "question_id": "example-01",
  "fact_inventory_complete": null,
  "atomic_facts": [],
  "answer_points": [
    {"id": "point-1", "text": "冻结的必要答案点", "covered": null, "rationale": null}
  ],
  "required_conditions": [
    {"id": "condition-1", "text": "冻结的必要实验条件", "covered": null, "rationale": null}
  ],
  "retrieval_sufficient": null,
  "unjustified_answer": null,
  "notes": null
}
```

评审按下面顺序填写：

1. **拆出实际输出的原子事实。** 阅读全部输出，不能只挑容易验证的句子。`atomic_facts` 的每项为 `{id,text,supported,rationale}`；`supported` 只允许 `true`、`false` 或 `null`，判断必须结合这条事实的实际引用及必要上下文。论文整体有这个事实，但所附引用不能支撑它，仍不能将其评为受引用支持。
2. **确认事实清单完整。** 只有所有实际事实都已列出，才将 `fact_inventory_complete` 置为 `true`；已回答输出不能有“完整但为空”的事实清单。清单完整与事实支持全部通过是两件事。
3. **检查答案点与条件。** 逐项判断输出正文是否正确说清该项，包括必要限定；仅在引文里出现而答案未解释，不算正文覆盖。每个 `covered` 判断说明依据或缺口。语义重合的条件可以同时属于答案点和条件指标，但同一指标里只计对应的一个冻结条目。
4. **诊断检索充分性。** `retrieval_sufficient` 表示本次实际检索到的完整上下文是否足以支持完整答案，不是答案本身是否写对，也不是命中了多少短关键词。若充分证据已经找齐却漏答，优先诊断生成与覆盖；若证据不足，再考虑检索改动。
5. **检查无据作答。** 对冻结为不足的题，独立检查整个输出有没有给出无充分依据的实质结论，填写 `unjustified_answer` 并解释。应用显示“不足”也不能自动视为这项已审通过，需核对其说明没有夹带无据结论。

已填写的事实支持、答案覆盖和条件覆盖必须有 `rationale`；检索充分性或无据作答判断必须有 `notes`。这些文字记录评审依据，程序无法验证评审者是否真正读过原文，关键结论仍需用户核对。

## 分开统计的指标

每个二元指标输出 `numerator`、`denominator`、`negative`、`pending`。分母已知且所有单位已判定时才输出 `rate`；否则 `rate=null`。`confirmed_fraction_of_all` 是“已确认的正例 / 全部预选单位”，有未审时只是下界，不能写成最终成绩。事实清单或条件判据尚未完整时，其真正分母未知，两个比例都保持 `null`，另列 `denominator_is_complete=false` 和缺失清单。

| 指标 | 分子与分母 | 如何处理未审、失败和部分回答 |
| --- | --- | --- |
| 完整有据回答 | 完整覆盖答案点、必要条件，全部实际事实受引用支持且引用来源检查通过的题 / 全部预选可答题 | 工程失败、拒答和 `partial_answer` 不计完整；缺失结果或独立评审不足保留 pending。不能只拿成功回答作分母 |
| 事实支持 | 独立评审判为有据的原子事实 / 已完整列出的输出原子事实 | 清单未完成时总分母未知；少输出可能让支持比例变高，必须同时报告完整答案与要点覆盖 |
| 答案点覆盖 | 正确写明的冻结要点 / 全部预选可答题的冻结要点 | 拒答与工程失败的要点为未交付；缺失结果与未审要点保留 pending |
| 必要条件覆盖 | 正确写明的条件 / 全部预选可答题的冻结条件 | 条件判据缺失单列；不能把没有标注当作无需条件 |
| 检索充分性 | 独立检查认为本次证据足以完整作答的题 / 全部预选可答题 | 没有实际检索材料或没有评审时 pending，不用程序片段命中率代替 |
| 误拒答 | 应可答但实际终态为不足的题 / 全部预选可答题 | 基于冻结可答性与实际终态的行为统计；工程失败单列，不冒充拒答；缺失结果 pending |
| 无据作答 | 独立确认无充分依据仍给出结论的题 / 全部预选不足题 | 未独立检查的不足说明仍 pending；无任何答案输出的工程失败不算无据作答，但仍是工程失败 |
| 工程失败 | 运行器失败或应用终态失败的题 / 全部预选题 | 预选后没有结果文件的题显示 missing/pending，不从分母删除 |
| 引用存在与归属 | 独立程序检查通过的已回答输出 / 已回答输出 | 另列实际引用检查数量；零引用不能自动记通过；此项不代表语义支持 |

`per_question` 保存上述状态与逐题指标，便于把总数追到具体输出。工程失败的确认是工作流行为证据，并不等于对原子事实作了语义判断；实际有残留回答时，事实仍可独立审查。

资源统计单列：已知调用小计、总调用量是否可确定、未知调用量的题数、已知 token 小计、用量未知的调用数、实际记录的每题耗时及缺失数、各币种已知费用小计和未知费用调用。所有预选题都参与资源缺失统计，包括模型/运行器失败与无结果的题。费用未知时总费用为 `null`；已知小计的 `0` 不能解释为零成本，不套用其他模型费率。平均耗时仅针对有记录的题，同时显示样本数量。

## 汇总与复核

```bash
uv run --locked python scripts/score_quality.py summarize \
  --run "$RUN" --rubric "$RUBRIC" \
  --review "$REVIEW" --output "$NEW_SUMMARY"
```

输出还记录审查文件 SHA256、评分代码当前 SHA、评分脚本 SHA256 和创建时间；实际推理运行的代码 SHA 来自原 `invocation.json`，两者不混淆。数据、代码或输出变更后另建运行/审查/汇总，保留此前失败与部分覆盖。`selection.json` 必须在提交题目前写入；脚本能检测绑定后变化，不能仅凭哈希证明某次选择未根据结果事后筛题。

实际交付时同时报告选择范围、完成与缺失题数、模型与运行版本、独立 AI 与人工审查状态、质量指标、资源消耗和未执行项。旧集回归、少量代表性真实运行、候选留出集准备是不同证据层级；不能把代表题运行说成整套新集已测。

本轮沿用同一 `provider_quota` 持久账本和累计 160 次授权上限。本工具不消耗额度；真实运行的选择仍需先读实际余量，不能因为评分脚本存在就扩大已执行范围或重置账本。

## 确定性验证

[契约测试](../tests/test_quality_scoring.py) 使用明确标注的合成运行，验证低信息量回答不能获得完整分、失败和缺失题不消失、pending 不被转换为通过或失败、错误前提可被有据反驳、部分回答不能算完整、未知费用不等于零、ID/数据/结果/条件注释漂移被拒绝，以及 CLI 只能写新文件。这些测试验证评分程序行为，不提供模型质量成绩。

代码与运行基础改动执行项目 `make check`；聚焦本模块可先执行 `uv run --locked pytest -q tests/test_quality_scoring.py`。最终整体执行证据由对应验证记录保存，文档中的命令不等于已执行记录。
