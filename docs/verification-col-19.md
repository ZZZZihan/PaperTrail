# COL-19 论文简介 demo 验证记录

2026-09-05，关联 [COL-19](https://linear.app/colife/issue/COL-19)。范围与预先固定的内容规则见 [简介规格](paper-introduction-demo.md)。用户产品与学习验收待本人试用。

## 实现基线

从 COL-18 最终交付 `f0a63ed1e6856657946ae9dae7b45fbd8607db58` 的干净工作区创建 `codex/col-19-paper-introduction-demo`，未修改原任务的人工验收状态。

## 已执行本地检查

- `make check`：通过。Ruff 检查与格式、TypeScript/Vite 构建、183 项后端测试、安装包/health/OpenAPI/docs smoke 与 `git diff --check` 通过。存在既有 Starlette 弃用提示。
- `uv run --locked python scripts/evaluate_introductions.py --prepare-only`：通过；三篇论文冻结来源复核及新运行快照已完成，未调用模型。
- 新来源审计测试初次发现失败结果仍先读取来源的次序问题，已调整为先判断终态，3 项测试通过。新预算测试初次直接构造了未哈希 scope，改为使用实际 `Budget.from_env()` 后通过；产品账本逻辑未改。
- 独立源码审查发现两项前端问题：把任务 UUID 与请求 UUID 混用，以及 AI 检查状态字符串不匹配。均已修正；连接不确定时先“确认上次提交结果”，确认失败后才重新生成。
- 前端在后端重启前的页面检查确认原 PDF、原问答历史仍可显示；390×844 布局可切换且无横向溢出。这些检查不能替代成功简介的浏览器验证。

## 运行前的本地数据

`data/diagnostics/col19-baseline-20260905T073812/before.json` 保存迁移前快照：3 篇论文、50 条既有问答、129 次调用及原 PDF 哈希。模型为 `gpt-5.6-sol`，同 scope 上限 160 次，费用未知。

## 已保留的真实失败运行

| 版本 / 核心提交 | Reflexion | ReAct | Toolformer |
| --- | --- | --- | --- |
| v1 / `e20657a` | `invalid_output`，未解析出 JSON 对象 | `invalid_citation`，公式符号引用不一致 | `model_timeout`，45 秒单次期限 |
| v2 / `eee1497` | `invalid_output`，JSON 对象解析仍失败 | `invalid_citation`，引用跨越所选片段边界 | `insufficient_evidence`，引用存在但多项语义未完整支持 |

原运行分别保存在 `data/diagnostics/introduction-runs/20260905T074112.160029Z-414cb276696646279b3bdd3f1707c515` 和 `data/diagnostics/introduction-runs/20260905T074636.363989Z-fe6fa0e7737e44859e9e28e3a4080866`。Reflexion v2 由真实浏览器重试按钮发起，运行器复用了该活跃任务；其余由本地应用接口提交。未删除、覆盖或改写失败结果，未把格式失败记成语义不足。

v2 使用完整 JSON 示例、纯文字引用要求，并将简介单次窗口提高为至少 90 秒；普通问答仍为原配置。v3 根据实际失败改为模型仅选择片段标识、程序填入原文，同时收紧正文范围。原文存在/归属及逐项语义支持标准保持不变。新增安全解析诊断只记录枚举、长度、首末字符码点和 JSON 错误位置，不记录原始模型响应。

迁移后已逐项确认 3 篇论文、50 条旧问答完整数据、原 PDF 哈希一致，129 条既有调用均保留，结果见同目录 `after-migration.json`。

成功简介的真实结果、浏览器和重启检查在下方追加实际证据；上方失败不因后续成功而改写。
