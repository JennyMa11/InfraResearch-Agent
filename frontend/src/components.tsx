import type { Evidence, ResearchRun, Source, TraceEvent } from "./types";

export function StatusPill({ status }: { status: string }) {
  return <span className={`status status--${status}`}>{status}</span>;
}

export function SourcesPanel({ sources }: { sources: Source[] }) {
  return (
    <section className="panel sources-panel" aria-labelledby="sources-title">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Knowledge base</p>
          <h2 id="sources-title">数据源</h2>
        </div>
        <span className="count">{sources.length}</span>
      </div>
      <div className="source-list">
        {sources.length === 0 && (
          <div className="empty">
            <span className="empty__mark">∅</span>
            <p>还没有资料。上传文档或连接公开仓库开始研究。</p>
          </div>
        )}
        {sources.map((source) => (
          <article className="source" key={source.id}>
            <span className="source__icon">{source.kind === "github" ? "⌘" : "⌁"}</span>
            <div className="source__body">
              <strong title={source.name}>{source.name}</strong>
              <small>
                {source.revision?.slice(0, 8) ??
                  String(source.metadata.embedding_model ?? source.kind)}
              </small>
              {source.error && <p className="error-text">{source.error}</p>}
            </div>
            <StatusPill status={source.status} />
          </article>
        ))}
      </div>
    </section>
  );
}

function EventLabel({ event }: { event: TraceEvent }) {
  const labels: Record<string, string> = {
    run_started: "运行开始",
    node_started: "节点进入",
    node_completed: "节点完成",
    plan_created: "计划生成",
    tool_completed: "工具返回",
    evidence_added: "证据登记",
    evidence_graded: "证据评分",
    query_rewritten: "查询重写",
    run_completed: "运行完成",
    run_failed: "运行失败",
  };
  return <>{labels[event.event_type] ?? event.event_type}</>;
}

export function Timeline({ events }: { events: TraceEvent[] }) {
  if (events.length === 0) {
    return <p className="muted">提交问题后，这里会实时显示 Agent 路径。</p>;
  }
  return (
    <ol className="timeline">
      {events.map((event) => (
        <li key={event.sequence} className={`timeline__item timeline__item--${event.event_type}`}>
          <span className="timeline__dot" />
          <div>
            <strong>
              <EventLabel event={event} />
            </strong>
            <span>{event.node ?? `#${event.sequence}`}</span>
          </div>
          {typeof event.data.duration_ms === "number" && (
            <time>{event.data.duration_ms.toFixed(0)} ms</time>
          )}
        </li>
      ))}
    </ol>
  );
}

export function Report({
  run,
  onCitation,
}: {
  run: ResearchRun;
  onCitation: (id: string) => void;
}) {
  const content = run.answer ?? "";
  const parts = content.split(/(\[S\d+])/g);
  return (
    <article className="report">
      {parts.map((part, index) =>
        /^\[S\d+]$/.test(part) ? (
          <button
            className="citation"
            key={`${part}-${index}`}
            type="button"
            onClick={() => onCitation(part.slice(1, -1))}
          >
            {part}
          </button>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </article>
  );
}

export function EvidenceDrawer({
  evidence,
  onClose,
}: {
  evidence: Evidence | null;
  onClose: () => void;
}) {
  if (!evidence) return null;
  return (
    <aside className="drawer" aria-label={`证据 ${evidence.id}`}>
      <button className="drawer__close" type="button" onClick={onClose} aria-label="关闭">
        ×
      </button>
      <p className="eyebrow">Evidence {evidence.id}</p>
      <h3>{evidence.locator}</h3>
      <div className="scorebar">
        <span style={{ width: `${Math.max(4, evidence.score * 100)}%` }} />
      </div>
      <small>检索分数 {evidence.score.toFixed(3)}</small>
      <pre>{evidence.content}</pre>
    </aside>
  );
}

export function Metrics({ run }: { run: ResearchRun }) {
  const metrics = run.metrics;
  const items = [
    ["端到端", `${metrics.total_latency_ms.toFixed(0)} ms`],
    ["TTFT", metrics.ttft_ms == null ? "N/A" : `${metrics.ttft_ms.toFixed(0)} ms`],
    ["Tokens", String(metrics.prompt_tokens + metrics.completion_tokens)],
    ["检索轮次", String(metrics.retrieval_rounds)],
    ["Agent 步数", String(metrics.agent_steps)],
    ["Provider", metrics.provider],
    ["向量后端", metrics.vector_backend],
    ["KV Cache", metrics.kv_cache_usage == null ? "N/A" : `${metrics.kv_cache_usage}%`],
  ];
  return (
    <dl className="metric-grid">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
