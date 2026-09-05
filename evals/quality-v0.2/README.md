# 单篇问答质量 v0.2 开发回归注释

此目录中的 [react-03-conditions.json](react-03-conditions.json) 是原 [开发集](../development-v0.1/questions.json) `react-03` 的条件评分注释，已在下一次该题真实应用运行前冻结。它仍属于已参与调试的开发回归，不是新的留出题，也不为新留出集提供成绩。

原题文字、两项 `expected_answer_points`、基础模型、HotpotQA 六例与 FEVER 三例的判据均保持不变。这里只把旧预期已经明确要求的两个条件单列：主要提示设置中的模型保持冻结；所选案例的 ReAct 轨迹由人工编写。不得把只出现在引文、但回答正文没有说明的条件记为完整覆盖，不覆盖历史失败或部分回答记录。

注释绑定 ReAct `2210.03629v3`、原问题文件和 manifest 的 SHA256。AI 在本机核对 PDF 哈希与全部 33 页提取文本哈希，并阅读 PDF 物理第 3、4 页的相关段落：第 2 节说明主要采用冻结的 PaLM-540B 做少样本提示，第 3.2 节说明选取案例后人工编写 ReAct 轨迹。短引文只用于定位，评分应读取相应完整段落。本次准备没有模型 API 调用；来源与规则的用户人工核对仍 pending。

本文件不记录尚未发生的运行和语义评分。得到新的实际 `RUN` 后，按 [独立质量评测契约](../../docs/quality-evaluation-v02.md) 创建审查表：

```bash
uv run --locked python scripts/score_quality.py template \
  --run "$RUN" --review-source ai \
  --rubric evals/quality-v0.2/react-03-conditions.json \
  --output "$NEW_REVIEW"

uv run --locked python scripts/score_quality.py summarize \
  --run "$RUN" --review "$REVIEW" \
  --rubric evals/quality-v0.2/react-03-conditions.json \
  --output "$NEW_SUMMARY"
```

审查表绑定此次实际输出、预选范围和条件注释哈希。独立 AI 必须逐条填写原子事实、原答案点与两项条件的支持/覆盖依据，随后才汇总；应用自己的支持或覆盖结论不能代填。人工审查使用独立的 `--review-source human` 表，不能由 AI 结果改名取得。保留实际代码 SHA、运行目录、审查与汇总文件；条件注释如需变更，应另建版本并说明原因，不能借变更放宽本次标准。
