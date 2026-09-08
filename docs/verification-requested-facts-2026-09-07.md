# COL-19 核心请求无据与辅助背景：离线修复验证

日期：2026-09-07，北京时间。实现与规格提交：**`78b03e54afef75f5f4a2b3bfcc5e394a4502a6cf`**，
分支 `codex/col-19-paper-introduction-demo`，问答版本 `evidence-qa-v8-requested-facts`。
下列最终检查均在该实现提交、干净工作区运行；之后仅补充验证与交接文档。
这是本地工程与离线行为交付，尚未验证真实模型的新提示输出，不是发布或人工产品验收。

## 原失败与固定判据

[2026-09-06 的真实失败](verification-live-e2e-2026-09-06.md)保留：
任务 `4695a98f-0b12-4959-89b8-1092f2622202` 的原结果仍为 `partial_answer`，恰当不足为 **0/1**。
本轮精确回归问题是：

> ReAct 论文是否报告了 GPT-4 模型训练所使用的 GPU 型号、数量和总训练电费？请给出具体数值。

原规划额外列出“是否使用或训练 GPT-4”“实验模型身份”“模型参数是否冻结/更新”，
并将这些背景记为已回答；真正请求的 GPU 型号、GPU 数量、总训练电费均未覆盖。
旧逻辑只要存在有据事实就返回部分回答，混淆了背景事实支持和请求回答。

离线 fixture **保留原来的六个规划项**，没有把问题简化成只有三个正确规划项。
固定核对输出将前三项重新分类为 `background`（有据），后三项为 `requested`（无支持关联）。
合成 PDF 与两条 PaLM/GPT-3、冻结/微调背景均显式标为 OFFLINE；这不是新的真实论文语义评测。

通过条件：三个请求逐项显示未找到足够支持；`coverage.status = unanswered`，
终态为 `insufficient_evidence`；两条有据背景保留且明确次要，引用仍可核对；
最终信息以“结论：证据不足。”结束，范围限于当前已检索证据。

## 实际改动

- 核对阶段严格分类每个规划及新增要点：`requested / qualification / background`。
  原问题决定类别，规划器添加的背景不能自动变成请求；核对器可补回遗漏的明确请求。
  类别缺失/无效或没有任何请求项时，返回检查失败。
- 先过滤无支持的事实并重排引用索引，再计算是否存在真正回应请求的内容。
  背景或必要条件单独有据不能改变不足终态。请求项尚未完整但已提供真实部分内容时仍返回部分回答；
  必要条件继续影响完整性，旁支背景不拉低完整答案。
- `claims.role` 区分答案与背景，并通过 API 与 PostgreSQL JSON 保留。
  网页先列核心缺项，随后展示“补充背景”与引用，最后逐项说明不足并收尾。
  空检索、空答案路径也列明缺项；旧历史的 role 可为 null，旧覆盖与状态不回填。
- 新增可重复执行的 `make check-browser`；Playwright 仅为锁定的开发依赖。
  调用次数、时限、检索参数、真实模型配置、现有 quota scope 和历史账本均未改动。

行为细节见 [质量规格](reading-quality-v02.md#2026-09-07核心请求与辅助背景分开判定)。

## 精确提交上的检查结果

| 检查 | 实际结果 |
| --- | --- |
| `make check` | 退出 0；**407/407 后端测试**、Ruff 检查与格式、TypeScript 与生产构建、HTTP/OpenAPI 冒烟、`git diff --check` 通过；npm 报告 0 vulnerabilities |
| `make check-e2e` | 退出 0；**16/16 检查**通过；7 条问答、16 次纯离线调用，包含精确 GPU 案例和既有六种结果场景 |
| API 精确 GPU 案例 | 三个缺失请求、两条 background claim、`unanswered` 与 `insufficient_evidence`，3 次 mock 调用；逐条检查引用身份、页码、原文和幂等复用 |
| API 重启 | 独立应用与 PostgreSQL 均重启，论文/页面/历史/问答完整对象、PDF 字节和调用追踪一致；临时资源已清理 |
| `make check-browser` | 退出 0；**7 组浏览器断言及资源清理**通过，Chromium `151.0.7922.34`；2 条问答、6 次 mock 调用，非本地请求与 page error 均为 0 |

浏览器通过真实表单上传合成 PDF、提交精确问题，检查三个缺项优先展示、两条背景清楚分区、
三个背景核对项不显示“已回答”，以及最终不足结论。两条背景引用分别打开 PDF 第 1、3 页，
canvas 可见，提取文字高亮与保存引文一致。浏览器重载、刷新历史和引用查看均未新增 POST，
完整保存结果相同。390×844 手机视口无横向溢出；桌面上部、下部及手机截图已实际查看。
对照题仍显示有据的 `partial_answer` 与剩余缺项。

API 重启快照 SHA-256：`f64890206c04203cc8e9d20a468a2df428434b8f0e276e22a508ed3b087da92e`。
两个 E2E JSON 均记录同一完整实现 SHA `78b03e54afef75f5f4a2b3bfcc5e394a4502a6cf`。

首轮完整后端检查曾为 **406 通过、1 失败**：新回归发现 API 响应模型过滤 `role` 字段。
补充 `QuestionClaim` 的可空字段后，407 项全部通过；最终上述提交再次执行并通过所有既定检查。
保留首轮失败日志。检查仍有一条原有 Starlette/anyio 弃用警告，npm 也提示部分安装脚本未配置 allowScripts；
构建和测试成功，本轮没有修改安装脚本授权策略。

## 证据位置与隔离

Git 忽略的本机证据：

- `output/e2e/requested-facts-20260907/`：`make-check-78b03e5.log`、
  `make-check-e2e-78b03e5.log`、`make-check-browser-78b03e5.log`、`api-78b03e5.json`，
  以及 `backend-before-api-contract-fix.log` 的首轮失败。
- `output/playwright/requested-facts-20260907-78b03e5/`：`browser.json`、
  `gpu-insufficient-desktop.png`、`gpu-insufficient-desktop-conclusion.png`、`gpu-insufficient-mobile.png`。

全部模型交互由 `httpx.MockTransport` 处理；临时 app、数据库、资料库和 OFFLINE 账本与真实环境分离。
浏览器每次写入前核对 fixture 标记，阻止非本地 origin 请求；没有使用真实 provider 凭据。
**本轮新增真实 provider 调用为 0**，未编辑 `.env.local`、真实 scope、额度上限或旧结果，
未重启真实应用/数据库。原 **407/407** 是 9 月 6 日记录，本轮未重新读取真实账本，不能写成新的余额测量。

复现需已有 PostgreSQL 17、锁定项目依赖和 Chromium：

```bash
make check
# 若本机尚未安装测试浏览器，先安装一次；该步骤下载浏览器，不调用模型。
web/node_modules/.bin/playwright install chromium
make check-e2e
make check-browser
```

## 剩余真实模型与交付边界

本轮用户明确禁止真实 provider 调用，因此**新提示的真实模型验证未执行**。
确定性通过只证明程序能正确处理给定的类别、关联和支持判断；不能证明模型会完整拆出请求，
正确区分 requested/background、识别直接反证、建立真实部分回答关联，或准确判断引用支持。
已有完整/部分回答及不足行为的真实回归质量也未重新测量。

后续获得新任务授权后，需沿原 scope 和届时实际配置重跑精确问题及可答/部分回答对照，
独立对照原文核对请求分类、事实支持、三个缺项与最终状态，保留失败和实际用量。
不得据此离线结果把 9 月 6 日的 **0/1** 改成真实通过，也不能改写旧开发集/留出集成绩。

Linear 已只读核对：COL-19 为 In Review、归属 P2，无阻塞依赖，与 COL-18 相关。
本次提交保留在本地现有分支，未推送、未合并，未声明远端 CI 通过或完成发布。
人工产品认可、P0 人工核对与学习验收继续 pending。
