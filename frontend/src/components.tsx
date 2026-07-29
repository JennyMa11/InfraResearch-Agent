import type {
  Evidence,
  ResearchRun,
  ResearchRunSummary,
  Source,
  TraceEvent,
} from "./types";

const MAX_VISIBLE_TRACE_EVENTS = 14;

export function isVisibleTraceEvent(event: TraceEvent) {
  return event.event_type !== "token";
}

export function StatusPill({ status }: { status: string }) {
  return <span className={`status status--${status}`}>{status}</span>;
}

export function SourcesPanel({
  sources,
  activeSourceId,
  onDelete,
  onReindex,
  onCancel,
  query,
  statusFilter,
  page,
  pages,
  total,
  onQueryChange,
  onStatusChange,
  onPageChange,
}: {
  sources: Source[];
  activeSourceId?: string | null;
  onDelete?: (source: Source) => void;
  onReindex?: (source: Source) => void;
  onCancel?: (source: Source) => void;
  query?: string;
  statusFilter?: string;
  page?: number;
  pages?: number;
  total?: number;
  onQueryChange?: (value: string) => void;
  onStatusChange?: (value: string) => void;
  onPageChange?: (page: number) => void;
}) {
  return (
    <section className="panel sources-panel" aria-labelledby="sources-title">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Knowledge base</p>
          <h2 id="sources-title">数据源</h2>
        </div>
        <span className="count">{total ?? sources.length}</span>
      </div>
      {onQueryChange && onStatusChange && (
        <div className="list-filters">
          <input
            aria-label="搜索数据源"
            value={query ?? ""}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="搜索名称或 URI"
          />
          <select
            aria-label="数据源状态"
            value={statusFilter ?? ""}
            onChange={(event) => onStatusChange(event.target.value)}
          >
            <option value="">全部状态</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="cancel_requested">cancel requested</option>
            <option value="cancelled">cancelled</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
          </select>
        </div>
      )}
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
            <div className="source__tail">
              <StatusPill status={source.status} />
              {onCancel &&
                source.active_ingestion_id &&
                ["pending", "running", "cancel_requested"].includes(source.status) && (
                  <button
                    type="button"
                    disabled={source.status === "cancel_requested"}
                    onClick={() => onCancel(source)}
                    aria-label={`取消 ${source.name}`}
                  >
                    停止
                  </button>
                )}
              {source.kind !== "issue" && (onDelete || onReindex) && (
                <div className="source__actions">
                  {onReindex && (
                    <button
                      type="button"
                      disabled={
                        activeSourceId === source.id ||
                        source.status === "pending" ||
                        source.status === "running"
                      }
                      onClick={() => onReindex(source)}
                      aria-label={`重建 ${source.name}`}
                    >
                      ↻
                    </button>
                  )}
                  {onDelete && (
                    <button
                      type="button"
                      disabled={
                        activeSourceId === source.id ||
                        source.status === "pending" ||
                        source.status === "running"
                      }
                      onClick={() => onDelete(source)}
                      aria-label={`删除 ${source.name}`}
                    >
                      ×
                    </button>
                  )}
                </div>
              )}
            </div>
          </article>
        ))}
      </div>
      {onPageChange && (pages ?? 0) > 1 && (
        <div className="pagination" aria-label="数据源分页">
          <button
            type="button"
            disabled={(page ?? 1) <= 1}
            onClick={() => onPageChange((page ?? 1) - 1)}
          >
            上一页
          </button>
          <span>
            {page}/{pages}
          </span>
          <button
            type="button"
            disabled={(page ?? 1) >= (pages ?? 0)}
            onClick={() => onPageChange((page ?? 1) + 1)}
          >
            下一页
          </button>
        </div>
      )}
    </section>
  );
}

export function ResearchHistory({
  runs,
  activeRunId,
  onOpen,
  onRetry,
  onCancel,
  query,
  statusFilter,
  page,
  pages,
  total,
  onQueryChange,
  onStatusChange,
  onPageChange,
}: {
  runs: ResearchRunSummary[];
  activeRunId?: string;
  onOpen: (run: ResearchRunSummary) => void;
  onRetry: (run: ResearchRunSummary) => void;
  onCancel?: (run: ResearchRunSummary) => void;
  query?: string;
  statusFilter?: string;
  page?: number;
  pages?: number;
  total?: number;
  onQueryChange?: (value: string) => void;
  onStatusChange?: (value: string) => void;
  onPageChange?: (page: number) => void;
}) {
  return (
    <section className="panel history-panel" aria-labelledby="history-title">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Recent research</p>
          <h2 id="history-title">研究历史</h2>
        </div>
        <span className="count">{total ?? runs.length}</span>
      </div>
      {onQueryChange && onStatusChange && (
        <div className="list-filters">
          <input
            aria-label="搜索研究历史"
            value={query ?? ""}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="搜索问题"
          />
          <select
            aria-label="研究状态"
            value={statusFilter ?? ""}
            onChange={(event) => onStatusChange(event.target.value)}
          >
            <option value="">全部状态</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="cancel_requested">cancel requested</option>
            <option value="cancelled">cancelled</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
          </select>
        </div>
      )}
      <div className="history-list">
        {runs.length === 0 && <p className="muted">还没有研究记录。</p>}
        {runs.map((run) => (
          <article
            className={`history ${run.id === activeRunId ? "history--active" : ""}`}
            key={run.id}
          >
            <button className="history__open" type="button" onClick={() => onOpen(run)}>
              <strong>{run.question}</strong>
              <small>
                {run.mode} · {run.provider}
              </small>
            </button>
            <div className="history__tail">
              <StatusPill status={run.status} />
              {run.status === "failed" && (
                <button type="button" onClick={() => onRetry(run)}>
                  重试
                </button>
              )}
              {onCancel &&
                ["pending", "running", "cancel_requested"].includes(run.status) && (
                  <button
                    type="button"
                    disabled={run.status === "cancel_requested"}
                    onClick={() => onCancel(run)}
                  >
                    取消
                  </button>
                )}
            </div>
          </article>
        ))}
      </div>
      {onPageChange && (pages ?? 0) > 1 && (
        <div className="pagination" aria-label="研究历史分页">
          <button
            type="button"
            disabled={(page ?? 1) <= 1}
            onClick={() => onPageChange((page ?? 1) - 1)}
          >
            上一页
          </button>
          <span>
            {page}/{pages}
          </span>
          <button
            type="button"
            disabled={(page ?? 1) >= (pages ?? 0)}
            onClick={() => onPageChange((page ?? 1) + 1)}
          >
            下一页
          </button>
        </div>
      )}
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
    run_cancelled: "运行取消",
  };
  return <>{labels[event.event_type] ?? event.event_type}</>;
}

export function Timeline({ events }: { events: TraceEvent[] }) {
  const withoutTokens = events.filter(isVisibleTraceEvent);
  const tokenCount = events.length - withoutTokens.length;
  const foldedCount = Math.max(0, withoutTokens.length - MAX_VISIBLE_TRACE_EVENTS);
  const visible =
    foldedCount > 0
      ? [
          ...withoutTokens.slice(0, 6),
          ...withoutTokens.slice(withoutTokens.length - 8),
        ]
      : withoutTokens;
  if (visible.length === 0) {
    return <p className="muted">提交问题后，这里会实时显示 Agent 路径。</p>;
  }
  return (
    <>
      {(tokenCount > 0 || foldedCount > 0) && (
        <p className="timeline__summary">
          {tokenCount > 0 ? `已隐藏 ${tokenCount} 个生成 token` : ""}
          {tokenCount > 0 && foldedCount > 0 ? "，" : ""}
          {foldedCount > 0 ? `已折叠 ${foldedCount} 个中间事件` : ""}
        </p>
      )}
      <ol className="timeline">
        {visible.map((event, index) => (
          <li
            key={event.sequence}
            className={`timeline__item timeline__item--${event.event_type}`}
          >
            {foldedCount > 0 && index === 6 && (
              <span className="timeline__fold">···</span>
            )}
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
    </>
  );
}

export function visibleAnswer(answer: string | null, evidence: Evidence[] = []) {
  const duplicateIds = new Set<string>();
  const seenEvidence = new Set<string>();
  evidence.forEach((item) => {
    const key = `${item.locator}\u0000${item.content.trim()}`;
    if (seenEvidence.has(key)) {
      duplicateIds.add(item.id);
    } else {
      seenEvidence.add(key);
    }
  });
  let cleaned = (answer ?? "")
    .replace(/<think>[\s\S]*?<\/think>\s*/gi, "")
    .replace(/<think>[\s\S]*$/gi, "")
    .trim();
  duplicateIds.forEach((id) => {
    cleaned = cleaned.replace(new RegExp(`\\s*\\[${id}\\]`, "g"), "");
  });
  return cleaned;
}

export function Report({
  run,
  onCitation,
}: {
  run: ResearchRun;
  onCitation: (id: string) => void;
}) {
  const content = visibleAnswer(run.answer, run.evidence);
  const markers = [...new Set(content.match(/\[S\d+]/g) ?? [])];
  return (
    <div className="report">
      <label htmlFor="direct-report-output">直接输出结果</label>
      <textarea
        id="direct-report-output"
        aria-label="直接输出结果"
        readOnly
        rows={Math.min(18, Math.max(7, content.split("\n").length + 2))}
        value={content}
      />
      {markers.length > 0 && (
        <div className="report__citations" aria-label="报告引用">
          <span>查看证据</span>
          {markers.map((marker) => (
            <button
              className="citation"
              key={marker}
              type="button"
              onClick={() => onCitation(marker.slice(1, -1))}
            >
              {marker}
            </button>
          ))}
        </div>
      )}
    </div>
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
