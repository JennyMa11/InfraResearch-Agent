import type {
  Ingestion,
  Page,
  ResearchRun,
  ResearchRunSummary,
  RunMode,
  Source,
  TraceEvent,
} from "./types";

const API = import.meta.env.VITE_API_URL ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `请求失败（HTTP ${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  listSources: ({
    page = 1,
    pageSize = 10,
    query = "",
    status = "",
    kind = "",
  } = {}) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (query) params.set("q", query);
    if (status) params.set("status", status);
    if (kind) params.set("kind", kind);
    return request<Page<Source>>(`/sources?${params}`);
  },

  uploadFile: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<Ingestion>("/sources/files", { method: "POST", body: data });
  },

  addGithub: (url: string, includeIssues: boolean) =>
    request<Ingestion>("/sources/github", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, include_issues: includeIssues }),
    }),

  addUrl: (url: string) =>
    request<Ingestion>("/sources/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),

  reindexSource: (id: string) =>
    request<Ingestion>(`/sources/${id}/reindex`, { method: "POST" }),

  deleteSource: (id: string) =>
    request<void>(`/sources/${id}`, { method: "DELETE" }),

  cancelIngestion: (id: string) =>
    request<Ingestion>(`/ingestions/${id}/cancel`, { method: "POST" }),

  createResearch: (question: string, mode: RunMode) =>
    request<ResearchRun>("/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode, top_k: 6 }),
    }),

  listResearch: ({
    page = 1,
    pageSize = 10,
    query = "",
    status = "",
    mode = "",
  } = {}) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (query) params.set("q", query);
    if (status) params.set("status", status);
    if (mode) params.set("mode", mode);
    return request<Page<ResearchRunSummary>>(`/research?${params}`);
  },

  getResearch: (id: string) => request<ResearchRun>(`/research/${id}`),

  retryResearch: (id: string) =>
    request<ResearchRun>(`/research/${id}/retry`, { method: "POST" }),

  cancelResearch: (id: string) =>
    request<ResearchRun>(`/research/${id}/cancel`, { method: "POST" }),

  subscribe(id: string, onEvent: (event: TraceEvent) => void, onEnd: () => void) {
    const stream = new EventSource(`${API}/research/${id}/events`);
    const names = [
      "run_started",
      "node_started",
      "node_completed",
      "plan_created",
      "tool_completed",
      "evidence_added",
      "evidence_graded",
      "query_rewritten",
      "token",
      "run_completed",
      "run_failed",
      "run_cancelled",
    ];
    names.forEach((name) =>
      stream.addEventListener(name, (raw) => {
        const event = JSON.parse((raw as MessageEvent).data) as TraceEvent;
        onEvent(event);
        if (
          name === "run_completed" ||
          name === "run_failed" ||
          name === "run_cancelled"
        ) {
          stream.close();
          onEnd();
        }
      }),
    );
    stream.onerror = () => {
      stream.close();
      onEnd();
    };
    return () => stream.close();
  },
};
