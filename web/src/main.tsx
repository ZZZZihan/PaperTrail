import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createRoot } from "react-dom/client";
import { QuestionPanel, type Citation } from "./QuestionPanel";
import { IntroductionPanel } from "./IntroductionPanel";
import "./style.css";

const PdfPage = lazy(() =>
  import("./PdfPage").then((module) => ({ default: module.PdfPage })),
);

class ReaderResourceBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed)
      return (
        <div className="loading-card inline-error" role="alert">
          阅读器资源已更新或加载失败，请刷新页面重试。
          <button
            type="button"
            className="text-button"
            onClick={() => window.location.reload()}
          >
            刷新页面
          </button>
        </div>
      );
    return this.props.children;
  }
}

type Paper = {
  id: string;
  filename: string;
  sha256: string;
  page_count: number;
  size_bytes: number;
  created_at: string;
  parser_version: string;
  empty_pages?: number[];
};
type Notice = { text: string; error: boolean } | null;
type Route = { id: string; page: number };

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    paper: (
      <>
        <path d="M6 3h8l4 4v14H6z" />
        <path d="M14 3v5h4M9 12h6M9 16h6" />
      </>
    ),
    library: (
      <>
        <rect x="3" y="4" width="5" height="16" rx="1" />
        <rect x="10" y="4" width="5" height="16" rx="1" />
        <path d="m17 5 4 14M4 8h3M11 8h3" />
      </>
    ),
    upload: (
      <>
        <path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5" />
      </>
    ),
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    back: <path d="m14 6-6 6 6 6" />,
    next: <path d="m10 6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    external: (
      <>
        <path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6" />
      </>
    ),
    close: <path d="m6 6 12 12M18 6 6 18" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.paper}
    </svg>
  );
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      body.error?.message || `请求未完成（${response.status}），请稍后重试。`,
    );
  }
  return response.json();
}

function readRoute(): Route {
  const values = new URLSearchParams(location.hash.slice(1));
  const page = Number(values.get("page"));
  return {
    id: values.get("paper") || "",
    page: Number.isInteger(page) && page >= 0 ? page : 0,
  };
}

function navigate(id = "", page = 0) {
  location.hash = id
    ? new URLSearchParams({ paper: id, page: String(page) }).toString()
    : "";
}

const fileSize = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.ceil(bytes / 1024)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const title = (filename: string) =>
  filename.replace(/\.pdf$/i, "").replace(/[_]/g, " ");

function highlightedText(text: string, quote?: string) {
  if (!quote) return text;
  // Extraction preserves line breaks; the server returns whitespace-normalized quotes.
  const pattern = quote.trim().split(/\s+/).map((word) =>
    word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  ).join("\\s+");
  if (!pattern) return text;
  const match = new RegExp(pattern).exec(text);
  if (!match) return text;
  return <>{text.slice(0, match.index)}<mark>{match[0]}</mark>{text.slice(match.index + match[0].length)}</>;
}

function Reader({
  route,
  onError,
}: {
  route: Route;
  onError: (text: string) => void;
}) {
  const [paper, setPaper] = useState<Paper | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [failure, setFailure] = useState("");
  const [tab, setTab] = useState("qa");
  const [readingTab, setReadingTab] = useState("introduction");
  const [questionFocusRequest, setQuestionFocusRequest] = useState(0);
  const [documentTab, setDocumentTab] = useState("pdf");
  const [citation, setCitation] = useState<Citation | null>(null);
  const [pageInput, setPageInput] = useState(String(route.page + 1));
  const original = useRef<HTMLElement>(null);
  const extracted = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (documentTab === "text" && citation?.page_index === route.page)
      extracted.current?.querySelector("mark")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [documentTab, text, citation, route.page]);

  useEffect(() => {
    const controller = new AbortController();
    setPaper(null);
    setFailure("");
    setReadingTab("introduction");
    setTab("qa");
    setCitation(null);
    request<Paper>(`/api/papers/${route.id}`, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setPaper(result);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFailure(error.message);
      });
    return () => controller.abort();
  }, [route.id]);

  useEffect(() => {
    setPageInput(String(route.page + 1));
    setText(null);
    if (!paper || paper.id !== route.id) return;
    if (route.page >= paper.page_count) {
      navigate(route.id, 0);
      return;
    }
    const controller = new AbortController();
    request<{ text: string }>(`/api/papers/${route.id}/pages/${route.page}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        setText(result.text);
        setFailure("");
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFailure(error.message);
      });
    return () => controller.abort();
  }, [paper, route.id, route.page]);

  function openCitation(value: Citation) {
    if (!paper || value.paper_id !== paper.id || !Number.isInteger(value.page_index) || value.page_index < 0 || value.page_index >= paper.page_count) {
      onError("引用无法对应到当前论文页面，请刷新后重试。");
      return;
    }
    setCitation(value);
    setDocumentTab("pdf");
    setTab("pdf");
    navigate(paper.id, value.page_index);
    if (window.matchMedia("(max-width: 900px)").matches)
      requestAnimationFrame(() => original.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  if (!paper)
    return (
      <div className="loading-card" role="status">
        {failure || "正在打开论文…"}
        <button className="text-button" onClick={() => navigate()}>
          返回论文库
        </button>
      </div>
    );
  return (
    <>
      <div className="reader-heading">
        <button className="text-button breadcrumb" onClick={() => navigate()}>
          <Icon name="back" size={16} /> 我的论文 <span>/</span>
        </button>
        <h1>{title(paper.filename)}</h1>
        <div className="paper-meta">
          <span className="status">
            <i /> 已解析并保存
          </span>
          <span>{paper.page_count} 页</span>
          <span>{fileSize(paper.size_bytes)}</span>
          <span>
            导入于 {new Date(paper.created_at).toLocaleDateString("zh-CN")}
          </span>
        </div>
      </div>
      {!!paper.empty_pages?.length && (
        <div className="warning">
          第 {paper.empty_pages.map((index) => index + 1).join("、")}{" "}
          页未提取到文字，页码已保留。请对照原文查看。
        </div>
      )}
      <div className="reader-toolbar">
        <div className="page-picker">
          <button
            aria-label="上一页"
            disabled={route.page === 0}
            onClick={() => navigate(paper.id, route.page - 1)}
          >
            <Icon name="back" size={17} />
          </button>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const page = Number(pageInput);
              if (
                Number.isInteger(page) &&
                page >= 1 &&
                page <= paper.page_count
              )
                navigate(paper.id, page - 1);
              else {
                setPageInput(String(route.page + 1));
                onError(`请输入 1 到 ${paper.page_count} 之间的页码。`);
              }
            }}
          >
            <label>
              PDF 第{" "}
              <input
                aria-label="PDF 页码"
                inputMode="numeric"
                value={pageInput}
                onChange={(event) => setPageInput(event.target.value)}
              />{" "}
              / {paper.page_count} 页
            </label>
          </form>
          <button
            aria-label="下一页"
            disabled={route.page + 1 >= paper.page_count}
            onClick={() => navigate(paper.id, route.page + 1)}
          >
            <Icon name="next" size={17} />
          </button>
        </div>
        <a
          className="text-button"
          href={`/api/papers/${paper.id}/file#page=${route.page + 1}`}
          target="_blank"
          rel="noreferrer"
        >
          打开原 PDF <Icon name="external" size={15} />
        </a>
      </div>
      <div className="mobile-tabs" role="tablist" aria-label="查看内容">
        <button
          role="tab"
          aria-selected={tab === "pdf"}
          onClick={() => setTab("pdf")}
        >
          PDF 原文
        </button>
        <button
          role="tab"
          aria-selected={tab === "qa"}
          onClick={() => setTab("qa")}
        >
          简介与问答
        </button>
      </div>
      <div className={`reader-grid show-${tab}`}>
        <section className="reader-panel original" ref={original} aria-label="论文原文">
          <div className="panel-heading">
            <div className="document-tabs" role="tablist" aria-label="原文显示方式">
              <button type="button" role="tab" aria-selected={documentTab === "pdf"} onClick={() => setDocumentTab("pdf")}>
                PDF 原文
              </button>
              <button type="button" role="tab" aria-selected={documentTab === "text"} onClick={() => setDocumentTab("text")}>
                提取文字
              </button>
            </div>
            <small>第 {route.page + 1} 页</small>
          </div>
          {citation?.page_index === route.page && (
            <div className="selected-evidence" role="status">
              <div>
                <strong>已定位引用 · PDF 第 {route.page + 1} 页</strong>
                <button type="button" aria-label="关闭当前引用提示" onClick={() => setCitation(null)}>×</button>
              </div>
              <q>{citation.quote}</q>
              {documentTab === "pdf" && (
                <button className="text-button" type="button" onClick={() => setDocumentTab("text")}>在提取文字中查看引用</button>
              )}
            </div>
          )}
          <div className={documentTab === "pdf" ? "" : "hidden-view"}>
            <ReaderResourceBoundary>
              <Suspense fallback={<div className="loading-card">正在打开阅读器…</div>}>
                <PdfPage key={paper.id} id={paper.id} page={route.page} />
              </Suspense>
            </ReaderResourceBoundary>
          </div>
          {documentTab === "text" && (
            <>
              <div className="extraction-note">
                文字顺序、图内文字、表格和公式可能有误，请以原 PDF 为准。
              </div>
              <div className="page-text" ref={extracted} key={`${route.id}-${route.page}`}>
                {failure ? (
                  <p className="inline-error" role="alert">{failure}</p>
                ) : text === null ? (
                  <p role="status">正在读取文字…</p>
                ) : text.trim() ? (
                  highlightedText(text, citation?.page_index === route.page ? citation.quote : undefined)
                ) : (
                  <div className="no-text">
                    <Icon name="paper" size={32} />
                    <p>这一页没有提取到文字</p>
                    <small>可能是空白页、图片或扫描内容，页码已保留。</small>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
        <section className="reader-panel reading-assistant" aria-label="论文研读">
          <div className="panel-heading reading-tabs-heading">
            <div className="document-tabs" role="tablist" aria-label="研读方式">
              <button type="button" role="tab" id="introduction-tab" aria-controls="introduction-view" aria-selected={readingTab === "introduction"} onClick={() => setReadingTab("introduction")}>
                论文简介
              </button>
              <button type="button" role="tab" id="questions-tab" aria-controls="questions-view" aria-selected={readingTab === "qa"} onClick={() => setReadingTab("qa")}>
                证据问答
              </button>
            </div>
            <small>基于当前论文</small>
          </div>
          <div id="introduction-view" role="tabpanel" aria-labelledby="introduction-tab" hidden={readingTab !== "introduction"}>
            <IntroductionPanel key={paper.id} paperId={paper.id} onCitation={openCitation} onAsk={() => {
              setReadingTab("qa");
              setQuestionFocusRequest((value) => value + 1);
            }} />
          </div>
          <div id="questions-view" role="tabpanel" aria-labelledby="questions-tab" hidden={readingTab !== "qa"}>
            <QuestionPanel key={paper.id} paperId={paper.id} onCitation={openCitation} focusRequest={questionFocusRequest} embedded />
          </div>
        </section>
      </div>
      <details className="provenance">
        <summary>文件与来源记录</summary>
        <dl>
          <dt>原文件名</dt>
          <dd>{paper.filename}</dd>
          <dt>文件 SHA-256</dt>
          <dd>{paper.sha256}</dd>
          <dt>解析版本</dt>
          <dd>{paper.parser_version}</dd>
          <dt>来源</dt>
          <dd>本地上传 · 显示页码按 PDF 文件顺序计算</dd>
        </dl>
      </details>
    </>
  );
}

function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [limits, setLimits] = useState({
    max_upload_bytes: 20 * 1024 * 1024,
    max_pages: 100,
  });
  const input = useRef<HTMLInputElement>(null);
  const uploading = useRef(false);

  async function loadPapers(offset = 0) {
    setLoading(true);
    setListError("");
    try {
      const batch = await request<Paper[]>(`/api/papers?offset=${offset}`);
      setPapers((current) => (offset ? [...current, ...batch] : batch));
      setHasMore(batch.length === 50);
    } catch (error) {
      setListError((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const listener = () => {
      setRoute(readRoute());
      setNotice(null);
    };
    window.addEventListener("hashchange", listener);
    void loadPapers();
    request<typeof limits>("/api/config")
      .then(setLimits)
      .catch(() => {});
    return () => window.removeEventListener("hashchange", listener);
  }, []);

  async function upload(files: FileList | File[]) {
    if (uploading.current || !files.length) return;
    const file = files[0];
    if (files.length > 1) {
      setNotice({ text: "每次请选择一篇 PDF。", error: true });
      return;
    }
    if (file.size > limits.max_upload_bytes) {
      setNotice({
        text: `文件超过 ${fileSize(limits.max_upload_bytes)}，请缩小后重试。`,
        error: true,
      });
      return;
    }
    uploading.current = true;
    setBusy(true);
    setNotice(null);
    setUploadName(file.name);
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await request<{ paper: Paper; duplicate: boolean }>(
        "/api/papers",
        { method: "POST", body },
      );
      // Set the route synchronously so the import result notice isn't cleared by hashchange.
      history.pushState(
        null,
        "",
        `#${new URLSearchParams({ paper: result.paper.id, page: "0" })}`,
      );
      setRoute({ id: result.paper.id, page: 0 });
      setNotice({
        text: result.duplicate
          ? "这篇论文已在库中，已为你打开原有记录。"
          : `已保存 ${result.paper.page_count} 页，可以开始核对原文。`,
        error: false,
      });
      void loadPapers();
    } catch (error) {
      setNotice({
        text:
          error instanceof TypeError
            ? "连接中断，请重新打开论文库确认保存结果后再试。"
            : (error as Error).message,
        error: true,
      });
    } finally {
      uploading.current = false;
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const choose = () => input.current?.click();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="#" className="brand" aria-label="PaperTrail 首页">
          <span className="brand-mark">
            <Icon name="paper" size={23} />
          </span>
          <span>
            PaperTrail<small>让理解有据可循</small>
          </span>
        </a>
        <div className="nav-label">工作空间</div>
        <button className="nav-item selected" onClick={() => navigate()}>
          <Icon name="library" /> 我的论文 <span className="nav-dot" />
        </button>
        <div className="sidebar-note">
          <span className="small-rule" />
          <p>
            每一个理解，
            <br />
            都可以回到原文。
          </p>
          <small>
            从一篇论文开始，
            <br />
            留下清晰的阅读路径。
          </small>
        </div>
        <div className="local-mode">
          <span className="avatar">我</span>
          <span>
            个人论文库
            <small>
              <i /> 本地工作空间
            </small>
          </span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            <Icon name="library" size={17} /> 我的论文库
          </span>
          <span className="topbar-note">阅读 · 理解 · 核对</span>
        </header>
        <input
          ref={input}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden-input"
          aria-label="选择 PDF 文件"
          onChange={(event) => {
            if (event.target.files) void upload(event.target.files);
          }}
        />
        <div className={`workspace ${route.id ? "reading" : ""}`}>
          {notice && (
            <div
              className={`notice ${notice.error ? "error" : ""}`}
              role={notice.error ? "alert" : "status"}
            >
              <Icon name={notice.error ? "paper" : "check"} size={18} />
              <span>{notice.text}</span>
              <button aria-label="关闭提示" onClick={() => setNotice(null)}>
                <Icon name="close" size={16} />
              </button>
            </div>
          )}
          {busy && (
            <div className="import-progress" role="status">
              <span className="spinner" />
              <div>
                正在导入论文
                <small>{uploadName} · 正在上传并提取逐页文字，请稍候</small>
              </div>
            </div>
          )}
          {route.id ? (
            <Reader
              key={route.id}
              route={route}
              onError={(text) => setNotice({ text, error: true })}
            />
          ) : (
            <>
              <div className="library-heading">
                <div>
                  <div className="eyebrow">YOUR READING TRAIL</div>
                  <h1>我的论文</h1>
                  <p>收好每一篇论文，让下一次阅读有迹可循。</p>
                </div>
                <span className="library-stamp">
                  READ.
                  <br />
                  TRACE.
                  <br />
                  UNDERSTAND.
                </span>
              </div>
              <div
                className={`upload-card ${dragging ? "dragging" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  void upload(event.dataTransfer.files);
                }}
              >
                <div className="upload-illustration" aria-hidden="true">
                  <div className="sheet back-sheet" />
                  <div className="sheet front-sheet">
                    <span>PDF</span>
                    <i />
                    <i />
                    <i />
                    <b>↗</b>
                  </div>
                </div>
                <div className="upload-copy">
                  <h2>把一篇论文放在这里</h2>
                  <p>
                    拖入 PDF，或选择本地文件。
                    <br className="mobile-break" />
                    保存原件，并按页查看提取文字。
                  </p>
                  <small>
                    可提取文本的 PDF · 最大 {fileSize(limits.max_upload_bytes)}{" "}
                    · 最多 {limits.max_pages} 页
                  </small>
                </div>
                <button
                  className="primary-button"
                  disabled={busy}
                  onClick={choose}
                >
                  <Icon name="upload" size={18} />{" "}
                  {busy ? "正在导入…" : "导入论文"}
                </button>
              </div>
              <div className="list-heading">
                <h2>
                  已保存的论文{" "}
                  <span>
                    {papers.length}
                    {hasMore ? "+" : ""}
                  </span>
                </h2>
                <button
                  className="text-button"
                  onClick={() => void loadPapers()}
                  disabled={loading}
                >
                  刷新列表
                </button>
              </div>
              {listError ? (
                <div className="loading-card inline-error" role="alert">
                  {listError}
                  <button
                    className="text-button"
                    onClick={() => void loadPapers()}
                  >
                    重试
                  </button>
                </div>
              ) : loading && !papers.length ? (
                <div className="loading-card" role="status">
                  正在读取论文库…
                </div>
              ) : !papers.length ? (
                <div className="empty-library">
                  <div className="empty-icon">
                    <Icon name="library" size={29} />
                  </div>
                  <h3>你的阅读路径，从这里开始</h3>
                  <p>
                    导入第一篇论文后，它会保存在这里。
                    <br />
                    随时回来，继续查看与核对。
                  </p>
                  <button className="text-button" onClick={choose}>
                    选择第一篇论文 <Icon name="arrow" size={16} />
                  </button>
                </div>
              ) : (
                <div className="paper-list">
                  {papers.map((paper) => (
                    <button
                      className="paper-row"
                      key={paper.id}
                      onClick={() => navigate(paper.id)}
                    >
                      <span className="file-icon">
                        <Icon name="paper" size={26} />
                        <small>PDF</small>
                      </span>
                      <span className="paper-info">
                        <strong>{title(paper.filename)}</strong>
                        <span>
                          {paper.page_count} 页 <b>·</b>{" "}
                          {fileSize(paper.size_bytes)} <b>·</b>{" "}
                          {new Date(paper.created_at).toLocaleDateString(
                            "zh-CN",
                          )}
                        </span>
                      </span>
                      <span className="status">
                        <i /> 已保存
                      </span>
                      <Icon name="arrow" size={19} />
                    </button>
                  ))}
                </div>
              )}
              {hasMore && (
                <button
                  className="load-more"
                  disabled={loading}
                  onClick={() => void loadPapers(papers.length)}
                >
                  {loading ? "读取中…" : "加载更多"}
                </button>
              )}
              <div className="library-footer">
                <Icon name="check" size={16} />
                <span>
                  原文件与逐页文字一同保存，刷新或重新打开后仍可查看。
                </span>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
