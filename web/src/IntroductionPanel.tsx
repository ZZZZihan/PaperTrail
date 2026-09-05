import { useEffect, useRef, useState } from "react";
import type { Citation } from "./QuestionPanel";

type Claim = { text: string; citations: Citation[] };
type Introduction = {
  summary: Claim;
  problem: Claim;
  contribution: Claim;
  mechanism: Claim;
  evidence_and_limits: Claim;
  terms: { term: string; explanation: string; citations: Citation[] }[];
};
type IntroductionTask = {
  id: string;
  paper_id: string;
  status: "pending" | "running" | "answered" | "insufficient_evidence" | "failed";
  stage: string;
  message: string;
  error_code: string | null;
  support_status?: string;
  created_at: string;
  completed_at: string | null;
  introduction: Introduction | null;
};
type ModelConfig = { configured: boolean; model: string | null; reason: string | null };

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.error?.message || `请求未完成（${response.status}），请稍后重试。`, response.status);
  }
  return response.json();
}

const inProgress = (task: IntroductionTask | null) =>
  task?.status === "pending" || task?.status === "running";

const stages: Record<string, string> = {
  pending: "简介任务已保存，等待处理…",
  queued: "简介任务已保存，等待处理…",
  retrieving: "正在查找论文的问题、方法与实验依据…",
  retrieval: "正在查找论文的问题、方法与实验依据…",
  generating: "正在整理研究问题、核心原理与关键术语…",
  generation: "正在整理研究问题、核心原理与关键术语…",
  validating: "正在校验引用是否来自当前论文…",
  citation_validation: "正在校验引用是否来自当前论文…",
  checking_support: "正在检查原文是否支持简介中的说法…",
  support_check: "正在检查原文是否支持简介中的说法…",
};

function Sources({ citations, paperId, onCitation }: {
  citations: Citation[];
  paperId: string;
  onCitation: (citation: Citation) => void;
}) {
  if (!citations.length) return null;
  const pages = [...new Set(citations.map((citation) => citation.page_index + 1))].sort((a, b) => a - b);
  return (
    <details className="introduction-sources">
      <summary>原文依据 · PDF 第 {pages.join("、")} 页</summary>
      <div className="claim-citations">
        {citations.map((citation, index) => (
          <button
            type="button"
            className="citation-link"
            key={`${citation.chunk_id}-${index}`}
            disabled={citation.paper_id !== paperId}
            onClick={() => onCitation(citation)}
            aria-label={`查看引用：PDF 第 ${citation.page_index + 1} 页，${citation.quote.slice(0, 60)}`}
          >
            <span className="citation-label">
              <span>PDF 第 {citation.page_index + 1} 页</span>
              <span aria-hidden="true">定位原文 ↗</span>
            </span>
            <q>{citation.quote}</q>
          </button>
        ))}
      </div>
    </details>
  );
}

export function IntroductionPanel({ paperId, onCitation, onAsk }: {
  paperId: string;
  onCitation: (citation: Citation) => void;
  onAsk: () => void;
}) {
  const [task, setTask] = useState<IntroductionTask | null>(null);
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [confirmingSubmission, setConfirmingSubmission] = useState(false);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(false);
  const sending = useRef(false);
  const pendingRequest = useRef<string | null>(null);
  const submission = useRef<AbortController | null>(null);
  const active = inProgress(task);
  const introduction = task?.status === "answered" ? task.introduction : null;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      submission.current?.abort();
    };
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
        if (!controller.signal.aborted) setConfigError("暂时无法读取模型状态，请检查连接后刷新。");
      });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let shouldPoll = false;
    async function load() {
      try {
        const result = await request<IntroductionTask | null>(`/api/papers/${paperId}/introduction`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (result && result.paper_id !== paperId) throw new Error("简介与当前论文不匹配。");
        setTask(result);
        setLoadError("");
        shouldPoll = inProgress(result);
      } catch {
        if (controller.signal.aborted) return;
        setLoadError("暂时无法读取简介，已显示的内容会保留。请检查连接后刷新。");
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (shouldPoll) timer = setTimeout(() => void load(), 2000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [paperId, revision]);

  async function generate() {
    if (sending.current || active || introduction || loading || loadError || !config?.configured || configError) return;
    sending.current = true;
    setSubmitting(true);
    setSubmitError("");
    const controller = new AbortController();
    submission.current = controller;
    // Reuse an uncertain request after a network failure; a confirmed failed task gets a new ID.
    pendingRequest.current ||= crypto.randomUUID();
    try {
      const result = await request<IntroductionTask>(`/api/papers/${paperId}/introduction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: pendingRequest.current }),
        signal: controller.signal,
      });
      if (!mounted.current || controller.signal.aborted) return;
      if (result.paper_id !== paperId) throw new Error("简介与当前论文不匹配。");
      setTask(result);
      pendingRequest.current = null;
      setConfirmingSubmission(false);
      setRevision((value) => value + 1);
    } catch (error) {
      if (!mounted.current || controller.signal.aborted) return;
      if (error instanceof ApiError) {
        if (error.status < 500) {
          pendingRequest.current = null;
          setConfirmingSubmission(false);
        } else setConfirmingSubmission(true);
        setSubmitError(error.message);
      } else {
        setConfirmingSubmission(true);
        setSubmitError("连接中断，暂时无法确认服务器是否已接收任务。请点击“确认上次提交结果”，会复用原请求核对，不会重复创建任务。");
      }
      setRevision((value) => value + 1);
    } finally {
      sending.current = false;
      if (mounted.current && !controller.signal.aborted) setSubmitting(false);
    }
  }

  const sections: { key: "problem" | "contribution" | "mechanism" | "evidence_and_limits"; number: string; title: string }[] = [
    { key: "problem", number: "01", title: "它试图解决什么问题" },
    { key: "contribution", number: "02", title: "作者提出了什么" },
    { key: "mechanism", number: "03", title: "核心原理是怎样的" },
    { key: "evidence_and_limits", number: "04", title: "结果与适用边界" },
  ];

  return (
    <section className="introduction-panel-content" aria-label="论文简介">
      <div className="introduction-heading">
        <div>
          <span className="introduction-eyebrow">读懂一篇论文的起点</span>
          <h2>{introduction ? "先把主要思路读明白" : "从问题出发，理解它的原理"}</h2>
        </div>
        <button type="button" className="text-button" onClick={() => setRevision((value) => value + 1)} disabled={submitting}>
          刷新
        </button>
      </div>

      {loading && <p className="introduction-loading" role="status">正在读取已保存的简介…</p>}
      {loadError && <p className="question-error" role="alert">{loadError}</p>}
      {submitError && <p className="question-error" role="alert">{submitError}</p>}

      {!loading && introduction && (
        <>
          <div className="introduction-status"><span className="ai-label">AI 生成 · 待你核对</span><span>已保存</span></div>
          <div className="introduction-summary">
            <span className="introduction-eyebrow">一句话概览</span>
            <p>{introduction.summary.text}</p>
            <Sources citations={introduction.summary.citations} paperId={paperId} onCitation={onCitation} />
          </div>
          <div className="introduction-sections">
            {sections.map((section) => (
              <section className={`introduction-section introduction-${section.key}`} key={section.key}>
                <h3><span aria-hidden="true">{section.number}</span>{section.title}</h3>
                <p>{introduction[section.key].text}</p>
                <Sources citations={introduction[section.key].citations} paperId={paperId} onCitation={onCitation} />
              </section>
            ))}
          </div>
          <section className="introduction-terms" aria-label="关键术语">
            <h3>读懂它，需要理解这些词</h3>
            <p className="introduction-terms-note">结合这篇论文解释，帮助你回到方法本身。</p>
            <dl>
              {introduction.terms.map((term, index) => (
                <div key={`${term.term}-${index}`}>
                  <dt>{term.term}</dt>
                  <dd>
                    <p>{term.explanation}</p>
                    <Sources citations={term.citations} paperId={paperId} onCitation={onCitation} />
                  </dd>
                </div>
              ))}
            </dl>
          </section>
          <div className="introduction-footer">
            <p>引用来自当前论文，已通过来源校验。{task?.support_status === "ai_checked" ? "AI 已检查原文支持关系；" : ""}简介仍待你对照原文核对。页码按 PDF 文件顺序计算。</p>
            <button type="button" className="text-button" onClick={onAsk}>还有不明白的地方？继续向论文提问 →</button>
          </div>
        </>
      )}

      {!loading && !introduction && (
        <>
          {active ? (
            <div className="introduction-progress" role="status">
              <span className="spinner" />
              <div><strong>{stages[task?.stage || ""] || "正在阅读证据、撰写简介并核对引用…"}</strong><p>完成后会自动显示。可以离开此页，稍后回来查看。</p></div>
            </div>
          ) : task?.status === "failed" || task?.status === "insufficient_evidence" ? (
            <div className="introduction-failure" role="alert">
              <h3>{task.status === "failed" ? "这次简介未能完成" : "当前已检索证据不足以形成简介"}</h3>
              <p>{task.message || "本次结果已保留，请检查服务状态后主动重试。"}</p>
              {task.status === "insufficient_evidence" && <p>这不表示整篇论文没有相关内容。你可以对照原文，或切换到证据问答，先问一个具体问题。</p>}
            </div>
          ) : (
            <div className="introduction-empty">
              <span className="introduction-bookmark" aria-hidden="true">读</span>
              <h3>先建立理解，再深入细节</h3>
              <p>用通俗中文串起研究问题、主要贡献和核心原理，解释影响理解的关键术语，并保留结果成立的条件。</p>
              <div className="introduction-outline" aria-label="简介包含的内容"><span>研究问题</span><span>方法原理</span><span>关键术语</span><span>原文依据</span></div>
            </div>
          )}
          {!active && (
            <div className="introduction-generate">
              <p>{confirmingSubmission
                ? "上次提交的接收情况尚未确认。先取回该请求的处理结果；确认失败后，可以再发起新的生成。"
                : "点击后，AI 会根据当前论文的已提取正文撰写简介。每一部分附有原文依据，生成结果自动保存，待你核对。"}</p>
              <button type="button" className="primary-button" onClick={() => void generate()} disabled={submitting || !!loadError || !config?.configured || !!configError}>
                {submitting ? (confirmingSubmission ? "正在确认…" : "正在提交…") : confirmingSubmission ? "确认上次提交结果 →" : task ? "重试生成简介 →" : "生成论文简介 →"}
              </button>
            </div>
          )}
          <div className={`model-status ${config?.configured && !configError ? "" : "unconfigured"}`}>
            {configError || (config === null ? "正在读取模型状态…" : config.configured
              ? `已配置模型：${config.model || "本地配置指定"} · 生成时调用模型`
              : config.reason || "尚未配置模型服务。完成本地模型配置并重启应用后，可生成论文简介。")}
            {(configError || (config && !config.configured)) && <button type="button" className="text-button" onClick={() => setRevision((value) => value + 1)}>刷新模型状态</button>}
          </div>
        </>
      )}
    </section>
  );
}
