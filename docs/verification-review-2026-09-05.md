# COL-19 提交前审查与扩展端到端验证

日期：2026-09-05。用户要求审查后提交代码，并增加端到端测试。

## 审查范围与实际改动

起点为干净工作区 `b66c5bf39c86b8e57e29fee5b09c83aa6f7b13ba`，分支 `codex/col-19-paper-introduction-demo`。审查按 PR #2 的实际基线 `origin/codex/col-18-evidence-qa-v01` 比较。实时读取 Linear 后确认 COL-19 为 In Review、没有阻塞依赖、属于 P2；PR 为 draft/open。纠正 README、handoff 和 development-workflow 中残留的 In Progress，handoff 当前简介版本改为实际 v10。原模型评分不改写。

三路独立审查分别检查生成/检索/覆盖、前端异步与引用、数据库/任务/预算。未发现本轮新增后端生产代码中能够复现的 P1/P2；确认并修复以下问题：

1. **手机简介入口无法定位提问框（P2）**：390×844 视口，从 ReAct 长简介底部点击“继续向论文提问”，切换后仍停在问答历史深处。复现时输入框 top=-1487.8px、bottom=-1375.4px，焦点为 BODY。现在 CTA 同时发起聚焦请求；QuestionPanel 在 React 提交并解除 hidden 后聚焦输入框并滚入视口。普通 tab 切换保留草稿和阅读位置。390×844 与 1440×1000 浏览器回归均确认输入框完整可见且获得焦点。
2. **离线浏览器 fixture 无法执行简介链路**：旧 mock 无条件读取 `question`，而简介提交的是 `passages`，因此抛出 KeyError。补齐简介生成、支持/覆盖核对、唯一一次内容修订，增加成功、修订、失败、不支持、慢响应及两种旧卡升级场景。四种内容来源均有明确 OFFLINE 样例。历史卡仅对精确匹配 SHA-256 的两份合成 PDF 播种，没有修改生产路由。
3. **新增 API 测试脚本的异常清理遗漏**：最终复审发现 supervisor 被外部 SIGKILL 后，Uvicorn 子进程可能仍占用端口。清理现在独立于 supervisor 退出状态处理本次创建的进程组，再停止私有数据库和删除临时目录。实际独立故障验证先杀死 supervisor，确认子应用、数据库及健康接口仍存活，再调用 cleanup；0.429 秒后两个子服务均退出、端口关闭、目录消失，重复 cleanup 也成功。

新增 `scripts/check_e2e.py` 和 `make check-e2e`，使 HTTP、解析、任务、引用、持久化及重启检查可以重复执行。每次写请求前核对 OFFLINE 标识，使用独立动态端口、临时 PostgreSQL 和论文库，并在结束时清理。详见 [本地开发说明](local-development.md)。它是 API 端到端测试，不能冒充浏览器或真实模型质量测试。

## 本地确定性检查

| 检查 | 本轮实际结果 |
| --- | --- |
| 起点 `make check` | 375 passed；Ruff、TypeScript/Vite、HTTP smoke、diff 检查通过 |
| 修复及 fixture 扩展后 `make check` | 383 passed；上述检查均通过，退出 0 |
| 独立后端局部回归 | 232 passed；另 5 个依赖数据库的用例由完整 make check 覆盖 |
| 独立结构变异探测 | 135 个简介结构及 63 个覆盖结构，共 198 次；接受合法值或 ModelError，0 个意外异常 |
| 新 fixture 用例 | 15 passed，已包含于 383；生成/修订/拒绝/错误/超时/缓存升级/幂等及精确合成文件边界 |
| `make check-e2e` | 15 项 API E2E 检查通过；真实模型调用 0 |
| 最终清理修复后局部验证 | Ruff check/format 与 API E2E 再次通过；未改动生产逻辑，不重复执行相同的 383 项后端测试 |

测试有一条现存 Starlette/anyio 弃用警告，不影响退出码。直接运行局部测试时曾因未配置测试数据库而发生 5 个 fixture setup 错误；完整 make check 使用独立数据库后全部通过，不把 setup 错误写成业务失败或跳过后通过。

API E2E 实际覆盖：3 页 PDF（中间空页）、重复上传同 ID、错误 PDF/空白问题/过长问题/NUL/无效 UUID/越界页、六种问答终态、请求 ID 复用与冲突、论文归属、完整历史与单任务一致、原件字节及 HTTP Range、SIGUSR2 后应用和 PostgreSQL PID 均变化、6 个任务与 13 次 OFFLINE 调用 trace 保持一致、退出清理。详细机器证据位于本机忽略目录 `output/e2e/latest.json`。

## 实际 Chromium 浏览器验证

使用本地已构建前端和真实 HTTP 服务；真实库只阅读现存资料，失败与生成场景使用独立 OFFLINE 服务。不是仅调用 TestClient。

| 路径 | 实际操作与结果 |
| --- | --- |
| 真实 ReAct 新卡 | 8 项内容的 22 个引用按钮逐个点击，PDF 物理页码与页码框一致、PDF canvas 可见；提取文字显示当前引文高亮 |
| 缓存与刷新 | 简介刷新和浏览器重载后仍是原卡；引用走查期间 POST 请求数为 0 |
| 手机与桌面提问入口 | 390×844 与 1440×1000 均聚焦并显示输入框；普通 tab 来回切换保留未提交草稿 |
| 合成上传与空页 | 浏览器上传 3 页 PDF，第 2 页空文字保留，第 3 页 Omega 可引用和高亮 |
| 六种问答终态 | 通过输入框实际提交正常、部分回答、不足、无效引用、超时和服务失败；分别呈现 answered / partial_answer / insufficient_evidence / invalid_citation / model_timeout / model_failure；失败不发布实质回答 |
| 部分回答 | 显示已回答的 Omega 页码与缺失的试验轮数，引用跳至第 3 页 |
| 响应丢失后重试 | Playwright 在服务已接收 POST 后丢弃响应；草稿保留。再次提交复用任务 c2906a39-0d0e-40e3-b40c-c5d441e35d18，任务总数仅增加 1；刷新后 7 条历史恢复 |
| 七种简介场景 | 成功 2 次 OFFLINE 调用；修订 4 次；服务失败 1 次；检查不支持 4 次后不足；慢响应 2 次；旧卡升级成功 2 次；旧卡升级失败 1 次且原卡逐字保留 |
| 处理中切换/刷新 | 单独慢响应样例观察 pending，切换到问答再返回并重载，原任务最终 answered，未重复生成，只有 2 次 OFFLINE 调用 |
| 简介结果重载 | 七场景逐个重载，任务 ID、正文、终态保持；失败与不足有明确可见提示 |

本轮浏览器脚本曾先于 React 的页码状态提交进行一次立即断言，并有一次空页文案定位拼写错误；改成等待真实可观察状态及实际文案后完成复测。CLI 的文件选择模态状态也影响过批量脚本，随后直接设置页面的文件输入并重跑七场景。上述为测试驱动问题，原工具输出仍保留，不记为应用产品缺陷。真实库浏览器 console 为 0 errors / 0 warnings；离线断线场景的网络错误为显式注入。

## 重启与真实资料完整性

额外对简介 OFFLINE 服务实际发送 SIGUSR2，重启应用与其私有 PostgreSQL。对全部表按完整字段排序序列化并 SHA-256 比较，同时比较全部 PDF 哈希：**8 篇、24 页、10 条任务、18 条 OFFLINE 调用、0 活跃任务**重启前后完全一致。随后浏览器再次读取已保存研读卡并在手机视口点击第 3 页引用、高亮原文。

真实库本轮开始与最终的全部数据表和原件哈希完全一致：**7 篇论文、168 页、141 条任务、394 条调用、1 条 alias、0 活跃任务**。未修改 `.env.local`、scope、模型、上限或原调用账本；本轮新增真实模型调用为 0，原费用仍未知。

本机原始证据在 `output/playwright/review-20260905/`：

- `live-before.json` / `live-after.json`：真实库各表及 PDF 哈希。
- `intro-before-restart.json` / `intro-after-restart.json`：简介临时库重启比较。
- `intro-all-tasks.json`、`offline-qa-tasks.json`、`api-e2e.json`：实际任务对象与 API 检查证据。
- `mobile-ask-before.png`、`ask-focus-390.png`、`ask-focus-1440.png`：手机问题和修复后的两种视口。
- `live-citation-highlight.png`、`intro-*.png`、`mobile-fixture-citation.png`：实际页面截图。

原始本机输出依照 gitignore 不提交；本文件记录可复核结果，`make check-e2e` 提供可重复的 API 验证。

## 验收边界

本轮仅修复交互和扩大确定性验证，没有调整检索、提示词或模型调用策略。真实模型质量仍沿用 [v0.2 验证](verification-quality-v02.md)：旧开发集严格完整 8/10，新留出 4/16 且 3/20 超时；模型完整性误判与条件遗漏没有由离线测试解决。人工产品认可、原文核对及学习验收仍 pending。PR #2 保持 draft，未合并、未发布。
