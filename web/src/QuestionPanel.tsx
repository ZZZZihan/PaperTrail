import { useEffect, useRef, useState, type FormEvent } from "react";

export type Citation = {
  chunk_id: string;
  paper_id: string;
  page_index: number;
  quote: string;
};

type Question = {
  id: string;
  paper_id: string;
  question: string;
  status: "pending" | "running" | "answered" | "partial_answer" | "insufficient_evidence" | "failed";
  stage: string;
  claims: { text: string; citations: Citation[] }[];
  message: string;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  support_status?: string;
  coverage?: {
    status: "complete" | "partial" | "unanswered";
    review_source: "ai" | "not_checked";
    items: { requirement: string; covered: boolean; claim_indices: number[] }[];
  } | null;
  trace?: Record<string, unknown>;
};

type ModelConfig = {
  configured: boolean;
  model: string | null;
  reason: string | null;
};

class ApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.error?.message || `请求未完成（${response.status}），请稍后重试。`,
      body.error?.code || "request_failed",
      response.status,
    );
  }
  return response.json();
}

const inProgress = (question: Question) =>
  question.status === "pending" || question.status === "running";

const statusLabels: Record<Question["status"], string> = {
  pending: "等待处理",
  running: "正在查找与核对证据",
  answered: "回答已保存",
  partial_answer: "部分回答 · 仍有要点待核对",
  insufficient_evidence: "当前已检索证据不足",
  failed: "本次问答未完成",
};

const stages: Record<string, string> = {
  pending: "问题已保存，等待处理…",
  queued: "问题已保存，等待处理…",
  running: "正在处理问题…",
  retrieving: "正在当前论文中查找证据…",
  retrieval: "正在当前论文中查找证据…",
  rewriting: "正在理解问题并准备检索…",
  translating: "正在理解问题并准备检索…",
  query_expansion: "正在理解问题并准备检索…",
  generating: "正在根据已检索证据整理回答…",
  generation: "正在根据已检索证据整理回答…",
  validating: "正在校验引用来源与原文片段…",
  citation_validation: "正在校验引用来源与原文片段…",
  checking_support: "正在检查引用是否支持回答…",
  verifying: "正在检查引用是否支持回答…",
  support_check: "正在检查引用是否支持回答…",
};

const suggestions = [
  { label: "研究问题", question: "这篇论文试图解决什么研究问题？请结合原文说明。" },
  { label: "核心方法", question: "这篇论文提出的方法有哪些关键步骤？" },
  { label: "实验条件", question: "论文的主要实验在什么模型、数据集和评估条件下进行？" },
  { label: "方法局限", question: "作者讨论了哪些局限？请区分作者原文和你的推断。" },
];

function QuestionCard({
  question,
  initiallyOpen,
  paperId,
  onCitation,
  onRetry,
  canRetry,
}: {
  question: Question;
  initiallyOpen: boolean;
  paperId: string;
  onCitation: (citation: Citation) => void;
  onRetry: (question: string) => void;
  canRetry: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen || inProgress(question));
  const active = inProgress(question);
  return (
    <details
      className={`question-card question-${question.status}`}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span className="question-summary">
          <strong>{question.question}</strong>
          <span className="question-meta">
            <span>{statusLabels[question.status]}</span>
            <time dateTime={question.created_at}>
              {new Date(question.created_at).toLocaleString("zh-CN", {
                month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
              })}
            </time>
          </span>
        </span>
      </summary>
      <div className="question-body">
        {active && (
          <div className="question-progress" role="status">
            <span className="spinner" />
            <div>
              {stages[question.stage] || "正在查找证据、生成回答并核对引用…"}
              <small>可以离开此页，稍后在历史记录中查看结果。</small>
            </div>
          </div>
        )}
        {(question.status === "answered" || question.status === "partial_answer") && (
          <>
            <div className="evidence-verification">
              来源已校验 · AI 已检查支持关系 · 待你核对
            </div>
            {!question.coverage && <p className="review-note">这条历史回答尚未检查要点与必要条件是否完整。</p>}
            {question.claims.map((claim, index) => (
              <div className="answer-claim" key={index}>
                <p>{claim.text}</p>
                <div className="claim-citations">
                  {claim.citations.map((citation, citationIndex) => (
                    <button
                      type="button"
                      className="citation-link"
                      key={`${citation.chunk_id}-${citationIndex}`}
                      disabled={citation.paper_id !== paperId}
                      onClick={() => onCitation(citation)}
                      aria-label={`查看引用：PDF 第 ${citation.page_index + 1} 页，${citation.quote.slice(0, 60)}`}
                    >
                      <span className="citation-label">
                        <span>PDF 第 {citation.page_index + 1} 页</span>
                        <span aria-hidden="true">查看原文 ↗</span>
                      </span>
                      <q>{citation.quote}</q>
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {question.message && <p className="answer-note">{question.message}</p>}
            <p className="review-note">
              AI 检查不代替你的核对。页码按 PDF 文件顺序计算，可能与论文印刷页码不同。
            </p>
          </>
        )}
        {question.coverage && (
          <div className="answer-coverage" aria-label="回答要点核对">
            <strong>{question.coverage.status === "complete" ? "回答要点已由 AI 核对" : "已回答与待核对的要点"}</strong>
            <ul>
              {question.coverage.items.map((item, index) => (
                <li key={index}>
                  <span>{item.covered ? "已回答" : "待核对"}</span>：{item.requirement}
                </li>
              ))}
            </ul>
            <small>核对范围是本次问题与已检索内容。AI 仍可能遗漏条件，请结合原文判断。</small>
          </div>
        )}
        {question.status === "insufficient_evidence" && (
          <div className="answer-insufficient">
            <p>{question.message || "在当前已检索证据中未找到足以支持回答的内容。"}</p>
            <small>这不表示整篇论文一定没有相关内容。可缩小问题，补充方法名、实验名或原文关键词后再问。</small>
          </div>
        )}
        {question.status === "failed" && (
          <div className="answer-failure" role="alert">
            <p>{question.message || "本次处理失败，问题记录已保留。请检查模型配置或连接后重试。"}</p>
            {question.error_code && <small>错误标识：{question.error_code}</small>}
          </div>
        )}
        {(question.status === "failed" || question.status === "insufficient_evidence" || question.status === "partial_answer") && (
          <button
            type="button"
            className="text-button retry-question"
            disabled={!canRetry}
            onClick={() => onRetry(question.question)}
          >
            {question.status === "failed" ? "重新提问" : "修改问题后再问"} →
          </button>
        )}
      </div>
    </details>
  );
}

export function QuestionPanel({
  paperId,
  onCitation,
  embedded = false,
}: {
  paperId: string;
  onCitation: (citation: Citation) => void;
  embedded?: boolean;
}) {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [draft, setDraft] = useState("");
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [pageCount, setPageCount] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(true);
  const sending = useRef(false);
  const pendingRequest = useRef<{ question: string; id: string } | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const active = questions.some(inProgress);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setConfigError("");
    request<{ model: ModelConfig }>("/api/config", { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setConfig(result.model || {
          configured: false, model: null, reason: "当前服务尚未提供模型配置，请更新并重启应用。",
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) setConfigError("暂时无法读取模型状态，请检查应用是否运行后刷新。");
      });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let shouldPoll = true;
    async function load() {
      try {
        const batches = await Promise.all(Array.from({ length: pageCount }, (_, page) =>
          request<Question[]>(`/api/papers/${paperId}/questions?offset=${page * 100}`, {
            signal: controller.signal,
          }),
        ));
        const result = batches.flat();
        if (controller.signal.aborted) return;
        setQuestions(result.filter((question) => question.paper_id === paperId));
        setHasMore(batches[batches.length - 1].length === 100);
        setHistoryError("");
        shouldPoll = result.some(inProgress);
      } catch {
        if (controller.signal.aborted) return;
        setHistoryError("历史记录暂时无法更新，已显示的记录会保留。请检查连接后刷新历史。");
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setLoadingOlder(false);
          if (shouldPoll) timer = setTimeout(() => void load(), 2000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [paperId, revision, pageCount]);

  function editQuestion(value: string) {
    setDraft(value);
    setSubmitError("");
    textarea.current?.focus();
    form.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const question = draft.trim();
    if (!question || sending.current || active || loading || !config?.configured || configError) return;
    sending.current = true;
    setSubmitting(true);
    setSubmitError("");
    // Keep the request ID when a connection breaks after the server may have accepted it.
    if (!pendingRequest.current || pendingRequest.current.question !== question)
      pendingRequest.current = { question, id: crypto.randomUUID() };
    try {
      const result = await request<Question>(`/api/papers/${paperId}/questions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, request_id: pendingRequest.current.id }),
      });
      if (!mounted.current) return;
      if (result.paper_id !== paperId) throw new Error("问答记录与当前论文不匹配，请刷新历史记录。");
      setQuestions((current) => [result, ...current.filter((item) => item.id !== result.id)]);
      setDraft((current) => current.trim() === question ? "" : current);
      pendingRequest.current = null;
      setRevision((current) => current + 1);
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof ApiError) {
        // A server error may occur after acceptance; retain the idempotency key in that case.
        if (error.status < 500) pendingRequest.current = null;
        setSubmitError(error.message);
      } else {
        setSubmitError("连接中断，问题草稿已保留。请先刷新历史确认处理状态；再次提交相同草稿会复用请求，避免重复创建。");
      }
      setRevision((current) => current + 1);
    } finally {
      sending.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  return (
    <section className="reader-panel question-panel" aria-label="论文证据问答">
      {!embedded && <div className="panel-heading">
        <span>证据问答 <span className="version-badge">v0.2</span></span>
        <small>仅基于当前论文</small>
      </div>}
      <div className="question-panel-content">
        <form className="question-composer" ref={form} onSubmit={(event) => void submit(event)}>
          <label htmlFor="paper-question">从你想理解的问题开始</label>
          <p className="composer-description">用中文提问，沿着原文证据核对答案。</p>
          <div className="question-suggestions" aria-label="问题示例">
            {suggestions.map((suggestion) => (
              <button
                type="button"
                key={suggestion.label}
                onClick={() => editQuestion(suggestion.question)}
              >
                {suggestion.label}
              </button>
            ))}
          </div>
          <textarea
            ref={textarea}
            id="paper-question"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="例如：作者报告的方法优势是在什么实验条件下成立的？"
            rows={4}
            maxLength={2000}
            aria-describedby="question-guidance model-status"
          />
          <div className="composer-actions">
            <small id="question-guidance">{draft.length} / 2000 · 问答自动保存</small>
            <button
              type="submit"
              className="primary-button"
              disabled={!draft.trim() || submitting || active || loading || !config?.configured || !!configError}
            >
              {submitting ? "正在提交…" : active ? "正在处理问题…" : "查找证据并回答 →"}
            </button>
          </div>
          <div id="model-status" className={`model-status ${config?.configured && !configError ? "" : "unconfigured"}`}>
            {configError || (config === null ? "正在读取模型状态…" : config.configured
              ? `已配置模型：${config.model || "本地配置指定"} · 提问会调用该模型`
              : config.reason || "尚未配置模型。请在本地 .env.local 中配置模型服务和密钥，重启应用后刷新。")}
            {(configError || (config && !config.configured)) && (
              <button type="button" className="text-button" onClick={() => setRevision((current) => current + 1)}>
                刷新模型状态
              </button>
            )}
          </div>
          {submitError && <p className="question-error" role="alert">{submitError}</p>}
        </form>

        <div className="question-history-heading">
          <h2>问答历史 <span>{questions.length}</span></h2>
          <button type="button" className="text-button" onClick={() => setRevision((current) => current + 1)}>
            刷新历史
          </button>
        </div>
        {historyError && <p className="question-error history-error" role="alert">{historyError}</p>}
        {loading ? (
          <p className="question-empty" role="status">正在读取已保存的问答…</p>
        ) : !questions.length ? (
          <div className="question-empty">
            <span className="empty-question-mark" aria-hidden="true">“</span>
            <h3>把一个疑问，留在这篇论文里</h3>
            <p>回答会附上原文片段和 PDF 页码。<br />点击引用，即可回到左侧原文核对。</p>
            <small>问题、答案和引用会保存在本地，刷新或重启后仍可查看。</small>
          </div>
        ) : (
          <div className="question-history">
            {questions.map((question, index) => (
              <QuestionCard
                key={question.id}
                question={question}
                initiallyOpen={index === 0}
                paperId={paperId}
                onCitation={onCitation}
                onRetry={(value) => {
                  pendingRequest.current = null;
                  editQuestion(value);
                }}
                canRetry={!submitting && !active}
              />
            ))}
            {hasMore && (
              <button
                type="button"
                className="load-more"
                disabled={loadingOlder}
                onClick={() => {
                  setLoadingOlder(true);
                  setPageCount((current) => current + 1);
                }}
              >
                {loadingOlder ? "正在读取…" : "加载更早问答"}
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
