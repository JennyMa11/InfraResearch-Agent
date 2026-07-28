import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import {
  EvidenceDrawer,
  Metrics,
  Report,
  SourcesPanel,
  StatusPill,
  Timeline,
} from "./components";
import type { Evidence, ResearchRun, RunMode, Source, TraceEvent } from "./types";

const EXAMPLE = "vLLM 的 prefix caching 如何工作，它会不会改变模型输出？";

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [question, setQuestion] = useState(EXAMPLE);
  const [mode, setMode] = useState<RunMode>("agentic");
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [githubUrl, setGithubUrl] = useState("");
  const [includeIssues, setIncludeIssues] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshSources = useCallback(async () => {
    try {
      setSources(await api.listSources());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法加载数据源");
    }
  }, []);

  useEffect(() => {
    void refreshSources();
  }, [refreshSources]);

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      await api.uploadFile(file);
      setMessage(`${file.name} 已进入索引队列`);
      await refreshSources();
      window.setTimeout(() => void refreshSources(), 1200);
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
      api.subscribe(
        created.id,
        (trace) =>
          setEvents((current) =>
            current.some((item) => item.sequence === trace.sequence)
              ? current
              : [...current, trace],
          ),
        async () => {
          try {
            const completed = await api.getResearch(created.id);
            setRun(completed);
            setEvents(completed.events);
          } catch (error) {
            setMessage(error instanceof Error ? error.message : "无法读取运行结果");
          } finally {
            setBusy(false);
          }
        },
      );
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
            <small>Agent v0.1.0</small>
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
            <SourcesPanel sources={sources} />
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
                    <span className="count">{events.length}</span>
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
