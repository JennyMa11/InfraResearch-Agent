import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import {
  EvidenceDrawer,
  Metrics,
  Report,
  ResearchHistory,
  SourcesPanel,
  StatusPill,
  Timeline,
  isVisibleTraceEvent,
} from "./components";
import type {
  Evidence,
  ResearchRun,
  ResearchRunSummary,
  RunMode,
  Source,
  TraceEvent,
} from "./types";

const EXAMPLE = "vLLM 的 prefix caching 如何工作，它会不会改变模型输出？";

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceQuery, setSourceQuery] = useState("");
  const [sourceStatus, setSourceStatus] = useState("");
  const [sourcePage, setSourcePage] = useState(1);
  const [sourcePages, setSourcePages] = useState(0);
  const [sourceTotal, setSourceTotal] = useState(0);
  const [question, setQuestion] = useState(EXAMPLE);
  const [mode, setMode] = useState<RunMode>("agentic");
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [history, setHistory] = useState<ResearchRunSummary[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPages, setHistoryPages] = useState(0);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [githubUrl, setGithubUrl] = useState("");
  const [includeIssues, setIncludeIssues] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activeSourceId, setActiveSourceId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const monitorCleanupRef = useRef<(() => void) | null>(null);

  const refreshSources = useCallback(async () => {
    try {
      const result = await api.listSources({
        page: sourcePage,
        query: sourceQuery,
        status: sourceStatus,
      });
      setSources(result.items);
      setSourcePages(result.pages);
      setSourceTotal(result.total);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法加载数据源");
    }
  }, [sourcePage, sourceQuery, sourceStatus]);

  const refreshHistory = useCallback(async () => {
    try {
      const result = await api.listResearch({
        page: historyPage,
        query: historyQuery,
        status: historyStatus,
      });
      setHistory(result.items);
      setHistoryPages(result.pages);
      setHistoryTotal(result.total);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法加载研究历史");
    }
  }, [historyPage, historyQuery, historyStatus]);

  useEffect(() => {
    void refreshSources();
    void refreshHistory();
    return () => monitorCleanupRef.current?.();
  }, [refreshHistory, refreshSources]);

  const hasActiveSource = sources.some(
    (source) =>
      source.status === "pending" ||
      source.status === "running" ||
      source.status === "cancel_requested",
  );

  useEffect(() => {
    if (!hasActiveSource) return;
    const timer = window.setInterval(() => void refreshSources(), 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveSource, refreshSources]);

  const monitorRun = useCallback(
    (id: string) => {
      monitorCleanupRef.current?.();
      let stopped = false;
      let polling = false;
      let unsubscribe: () => void = () => undefined;
      const stop = () => {
        if (stopped) return;
        stopped = true;
        unsubscribe();
        window.clearInterval(timer);
        monitorCleanupRef.current = null;
      };
      const poll = async () => {
        if (stopped || polling) return;
        polling = true;
        try {
          const latest = await api.getResearch(id);
          setRun(latest);
          setEvents(latest.events);
          if (
            latest.status === "completed" ||
            latest.status === "failed" ||
            latest.status === "cancelled"
          ) {
            stop();
            setBusy(false);
            await refreshHistory();
          }
        } catch (error) {
          setMessage(error instanceof Error ? error.message : "无法读取运行结果");
        } finally {
          polling = false;
        }
      };
      const timer = window.setInterval(() => void poll(), 800);
      unsubscribe = api.subscribe(
        id,
        (trace) =>
          setEvents((current) =>
            current.some((item) => item.sequence === trace.sequence)
              ? current
              : [...current, trace],
          ),
        () => void poll(),
      );
      monitorCleanupRef.current = stop;
      void poll();
    },
    [refreshHistory],
  );

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      await api.uploadFile(file);
      setMessage(`${file.name} 已进入索引队列`);
      await refreshSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function addRepository(event: FormEvent) {
    event.preventDefault();
    if (!githubUrl.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.addGithub(githubUrl.trim(), includeIssues);
      setGithubUrl("");
      setMessage("仓库已进入索引队列；大型仓库可能需要几分钟。");
      await refreshSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "仓库导入失败");
    } finally {
      setBusy(false);
    }
  }

  async function reindexSource(source: Source) {
    setActiveSourceId(source.id);
    setMessage(null);
    try {
      await api.reindexSource(source.id);
      setMessage(`${source.name} 已进入重建队列`);
      await refreshSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重建失败");
    } finally {
      setActiveSourceId(null);
    }
  }

  async function deleteSource(source: Source) {
    if (!window.confirm(`删除数据源“${source.name}”？历史报告中的证据快照会保留。`)) {
      return;
    }
    setActiveSourceId(source.id);
    setMessage(null);
    try {
      await api.deleteSource(source.id);
      setMessage(`${source.name} 已从检索索引删除`);
      await refreshSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除失败");
    } finally {
      setActiveSourceId(null);
    }
  }

  async function cancelSource(source: Source) {
    if (!source.active_ingestion_id) return;
    setActiveSourceId(source.id);
    setMessage(null);
    try {
      await api.cancelIngestion(source.active_ingestion_id);
      setMessage(`${source.name} 的导入取消请求已提交`);
      await refreshSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "取消导入失败");
    } finally {
      setActiveSourceId(null);
    }
  }

  async function openHistory(item: ResearchRunSummary) {
    monitorCleanupRef.current?.();
    setBusy(
      item.status === "pending" ||
        item.status === "running" ||
        item.status === "cancel_requested",
    );
    setMessage(null);
    try {
      const selected = await api.getResearch(item.id);
      setRun(selected);
      setEvents(selected.events);
      setQuestion(selected.question);
      setMode(selected.mode);
      if (
        selected.status === "pending" ||
        selected.status === "running" ||
        selected.status === "cancel_requested"
      ) {
        monitorRun(selected.id);
      }
    } catch (error) {
      setBusy(false);
      setMessage(error instanceof Error ? error.message : "无法恢复研究记录");
    }
  }

  async function retryHistory(item: ResearchRunSummary) {
    setBusy(true);
    setMessage(null);
    setEvents([]);
    try {
      const created = await api.retryResearch(item.id);
      setRun(created);
      setQuestion(created.question);
      setMode(created.mode);
      await refreshHistory();
      monitorRun(created.id);
    } catch (error) {
      setBusy(false);
      setMessage(error instanceof Error ? error.message : "研究重试失败");
    }
  }

  async function cancelHistory(item: ResearchRunSummary) {
    setMessage(null);
    try {
      const cancelled = await api.cancelResearch(item.id);
      if (run?.id === item.id) {
        setRun(cancelled);
        setEvents(cancelled.events);
        if (cancelled.status === "cancelled") {
          setBusy(false);
          monitorCleanupRef.current?.();
        }
      }
      setMessage("研究取消请求已提交");
      await refreshHistory();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "取消研究失败");
    }
  }

  async function research(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setRun(null);
    setEvents([]);
    setMessage(null);
    try {
      const created = await api.createResearch(question.trim(), mode);
      setRun(created);
      await refreshHistory();
      monitorRun(created.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "研究任务创建失败");
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand__mark">IR</span>
          <span>
            <strong>InfraResearch</strong>
            <small>Agent v0.2.0</small>
          </span>
        </a>
        <div className="system-state">
          <span className="pulse" />
          Local-first workspace
        </div>
      </header>

      <main>
        <section className="hero">
          <div>
            <p className="eyebrow">Evidence-led technical research</p>
            <h1>
              从代码与文档中，
              <br />
              找到<span>可验证的答案</span>。
            </h1>
          </div>
          <p className="hero__intro">
            让 Agent 规划检索、评价证据并生成逐条可追溯的技术报告。
            每一步工具调用与性能数据都清晰可见。
          </p>
        </section>

        {message && (
          <div className="notice" role="status">
            {message}
            <button type="button" onClick={() => setMessage(null)}>
              ×
            </button>
          </div>
        )}

        <div className="workspace">
          <aside className="sidebar">
            <SourcesPanel
              sources={sources}
              activeSourceId={activeSourceId}
              onDelete={(source) => void deleteSource(source)}
              onReindex={(source) => void reindexSource(source)}
              onCancel={(source) => void cancelSource(source)}
              query={sourceQuery}
              statusFilter={sourceStatus}
              page={sourcePage}
              pages={sourcePages}
              total={sourceTotal}
              onQueryChange={(value) => {
                setSourceQuery(value);
                setSourcePage(1);
              }}
              onStatusChange={(value) => {
                setSourceStatus(value);
                setSourcePage(1);
              }}
              onPageChange={setSourcePage}
            />
            <ResearchHistory
              runs={history}
              activeRunId={run?.id}
              onOpen={(item) => void openHistory(item)}
              onRetry={(item) => void retryHistory(item)}
              onCancel={(item) => void cancelHistory(item)}
              query={historyQuery}
              statusFilter={historyStatus}
              page={historyPage}
              pages={historyPages}
              total={historyTotal}
              onQueryChange={(value) => {
                setHistoryQuery(value);
                setHistoryPage(1);
              }}
              onStatusChange={(value) => {
                setHistoryStatus(value);
                setHistoryPage(1);
              }}
              onPageChange={setHistoryPage}
            />
            <section className="panel importer">
              <p className="eyebrow">Add context</p>
              <h2>导入资料</h2>
              <input
                ref={fileRef}
                type="file"
                id="file"
                accept=".pdf,.md,.txt,.py,.ts,.tsx,.js,.rs,.go,.c,.cpp,.cu,.yaml,.yml,.toml"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void upload(file);
                }}
              />
              <label className="upload" htmlFor="file">
                <span>＋</span>
                <strong>上传 PDF、文档或代码</strong>
                <small>单个文件最大 25 MB</small>
              </label>
              <div className="or"><span>或</span></div>
              <form onSubmit={addRepository}>
                <input
                  type="url"
                  value={githubUrl}
                  onChange={(event) => setGithubUrl(event.target.value)}
                  placeholder="https://github.com/owner/repo"
                  aria-label="GitHub 仓库 URL"
                />
                <label className="check">
                  <input
                    type="checkbox"
                    checked={includeIssues}
                    onChange={(event) => setIncludeIssues(event.target.checked)}
                  />
                  同时索引 Issue
                </label>
                <button className="button button--secondary" disabled={busy} type="submit">
                  连接公开仓库
                </button>
              </form>
            </section>
          </aside>

          <section className="research-column">
            <form className="question-card" onSubmit={research}>
              <label htmlFor="question">研究问题</label>
              <textarea
                id="question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                rows={4}
                placeholder="询问架构、实现、性能或故障原因…"
              />
              <div className="question-card__footer">
                <div className="segmented" aria-label="RAG 模式">
                  {(["agentic", "naive"] as RunMode[]).map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={mode === item ? "active" : ""}
                      onClick={() => setMode(item)}
                    >
                      {item === "agentic" ? "Agentic RAG" : "Naive RAG"}
                    </button>
                  ))}
                </div>
                <button className="button button--primary" disabled={busy} type="submit">
                  {busy ? "研究中…" : "开始研究"} <span>→</span>
                </button>
                {run &&
                  ["pending", "running", "cancel_requested"].includes(run.status) && (
                    <button
                      className="button button--secondary"
                      disabled={run.status === "cancel_requested"}
                      type="button"
                      onClick={() =>
                        void cancelHistory({
                          id: run.id,
                          question: run.question,
                          mode: run.mode,
                          status: run.status,
                          provider: run.metrics.provider,
                          created_at: run.created_at,
                          completed_at: run.completed_at,
                        })
                      }
                    >
                      取消研究
                    </button>
                  )}
              </div>
            </form>

            <div className="result-grid">
              <section className="panel result">
                <div className="panel__header">
                  <div>
                    <p className="eyebrow">Technical report</p>
                    <h2>研究报告</h2>
                  </div>
                  {run && <StatusPill status={run.status} />}
                </div>
                {!run && (
                  <div className="result-placeholder">
                    <span>⌁</span>
                    <p>报告会在这里生成，并以 [S1] 标记链接到原始证据。</p>
                  </div>
                )}
                {run?.error && <p className="error-text">{run.error}</p>}
                {run?.answer && (
                  <Report
                    run={run}
                    onCitation={(id) =>
                      setSelectedEvidence(run.evidence.find((item) => item.id === id) ?? null)
                    }
                  />
                )}
                {run?.status === "completed" && <Metrics run={run} />}
              </section>

              <aside className="trace-stack">
                <section className="panel">
                  <div className="panel__header">
                    <div>
                      <p className="eyebrow">Live trace</p>
                      <h2>Agent 路径</h2>
                    </div>
                    <span className="count">{events.filter(isVisibleTraceEvent).length}</span>
                  </div>
                  {run?.plan.subquestions.length ? (
                    <ol className="plan">
                      {run.plan.subquestions.map((item, index) => (
                        <li key={`${item.question}-${index}`}>
                          <span>{index + 1}</span>
                          <p>{item.question}</p>
                          <small>{item.expected_evidence}</small>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  <Timeline events={events} />
                </section>
                {run?.tool_calls.length ? (
                  <section className="panel tools">
                    <p className="eyebrow">Tool calls</p>
                    <h2>检索详情</h2>
                    {run.tool_calls.map((tool) => (
                      <div className="tool" key={tool.id}>
                        <div>
                          <strong>{tool.name}</strong>
                          <small>{tool.result_summary}</small>
                        </div>
                        <time>{tool.duration_ms.toFixed(0)} ms</time>
                      </div>
                    ))}
                  </section>
                ) : null}
              </aside>
            </div>
          </section>
        </div>
      </main>

      <EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} />
    </div>
  );
}
