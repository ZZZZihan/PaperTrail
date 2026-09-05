# COL-16 本地验证记录

日期：2026-09-05。分支：`codex/col-16-pdf-import`。文档/实现版本以本文件对应 Git 提交为准。此记录区分自动检查、AI 开发核对、用户验收和发布。

## 实现与当前证据

已实现本地 PDF 上传、哈希去重、PostgreSQL/文件保存、逐页文本查询、原 PDF 查看及 React 网页。运行参数：20 MiB、100 页、20 秒解析时限；单进程最多 2 份同时导入。规格见 [pdf-import.md](pdf-import.md)。

- 后端隔离数据库检查：18 项测试通过，覆盖正常导入、原件字节一致、空页位置、重开应用、同名不同内容、重复与并发去重、损坏/加密/无文本、资源上限、子进程超时、写入失败回滚、原件缺失和数据库错误。
- HTTP/OpenAPI 冒烟通过。Ruff 静态和格式检查通过。
- React/TypeScript 类型检查和 Vite 生产构建通过。PDF.js 升级至 `6.3.289`，`npm audit --audit-level=high` 返回 0 vulnerabilities。
- 完整 `make check` 通过（18 tests passed，1 条依赖弃用警告）；35 个本地 Markdown 链接检查通过。
- `uv build --wheel` 成功，确认安装包包含 `papertrail/schema.sql`。网页运行仍按仓库内 `make dev`，未声称独立 wheel 包含前端。

测试环境：macOS / Apple Silicon；Python 3.12.12、uv 0.10.0、PostgreSQL 17.11、Node.js 26.7.0、pypdf 6.17.0、psycopg 3.3.5。安装使用锁文件。Starlette 测试客户端出现一条 AnyIO BlockingPortal 弃用警告，检查通过；未通过隐藏警告改变结果。

## 真实论文开发核对

使用 [ReAct: Synergizing Reasoning and Acting in Language Models, arXiv 2210.03629v3](https://arxiv.org/abs/2210.03629v3)，[PDF 固定版本](https://arxiv.org/pdf/2210.03629v3)。该文仅作为 AI 选取的开发样本，不是用户选定/人工核对的 P0 题集。

- 原件：633,805 字节，33 页。
- SHA-256：`f285b0971ae4a790e402fb93966bed3adde2cf0a04977d08b2b40d6ab0cace69`。
- 一次本地解析测量：0.449 秒，33 页均有非空输出；不是通用性能结论或完整质量验收。
- 渲染并对照第 1 页和第 2 页，检查标题/摘要、示意图、正文与页码；提取文本另抽查第 5、10 页的首尾。第 1 页内容和 PDF 物理页对应，第 2 页示意图的部分字体提取为异常字符。全文布局和全部公式未逐项验收。
- 因此保留原件对照和明确提示，不把非空输出记为正确理解，也不将图表提取结果用作已核实的问答证据。

原 PDF、解析文本、来源清单和截图保存在忽略目录 `data/samples/`、`output/playwright/`，不纳入公开源码。

## 浏览器与持久化

已从浏览器实际选择并上传 ReAct 原件，显示 33 页，进入原 PDF/提取文字对照视图，初次页面控制台 0 errors / 0 warnings。

Chromium 实际操作：

- 浏览器选择真实 PDF，POST 返回 201，显示 33 页和原件/提取文字。
- 点击下一页和输入页码跳到第 5 页；刷新后仍显示同一论文第 5 页。
- 在 390 × 844 视口切换 PDF 原文与提取文字，截图确认内容可见；`scrollWidth == innerWidth == 390`，无横向溢出。桌面 1440 × 1000 使用并排视图。
- 上传整篇空白技术 PDF，显示“未提取到可用文本”，列表仍为 1 篇。浏览器记录该预期 HTTP 422 的资源错误，没有应用 JavaScript 异常。
- 重传 ReAct 文件，返回 200 和“这篇论文已在库中”，仍为同一论文标识。
- 上传含中间空白页的 3 页技术 PDF，第 2 页显示空白提示，第 3 页仍对应 `Omega on physical page three`，页码未前移。测试后只删除本轮创建的该技术记录与原件，论文库保留 ReAct 开发样本。

持久化验证：停止 FastAPI 和专用 PostgreSQL，再通过 `make dev` 的完整启动流程启动（npm 使用已缓存依赖）。数据库 PID 已改变；列表条数仍为 1，论文元数据完全一致，下载原件的 SHA-256 不变，第 5 页文字与重启前逐字相同。浏览器再次打开该页，原件与文本正常显示。应用最终监听 `127.0.0.1:8000`，数据库监听 `127.0.0.1:55432`。

证据：`data/samples/restart-result.json`、`output/playwright/reader-after-restart.png`、`mobile-reader.png`、`mobile-text.png`、`empty-pdf-error.png`、`blank-page.png`。均为本地忽略文件；本记录保存可复核步骤和结果。

## 尚未完成的验收

- 用户真实阅读经历、P0 人工问答样例与用户学习复述。
- 远端 GitHub Actions、独立 Linux 环境运行和外部部署。
- 固定检索、模型问答及语义支持评测；没有准确率成绩。
- 扫描件 OCR、复杂图表/公式可靠恢复、后台持久任务和崩溃后的自动清理。
- 自动论文查找与下载已记录 COL-17，尚未实现。
