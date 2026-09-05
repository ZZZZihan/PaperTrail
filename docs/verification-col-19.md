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

真实模型、成功简介浏览器检查及重启结果将在同一记录追加实际证据；此版本尚不声明这些检查通过。
