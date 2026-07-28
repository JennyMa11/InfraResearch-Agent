import type { ResearchRun, RunMode, Source, TraceEvent } from "./types";

const API = import.meta.env.VITE_API_URL ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listSources: () => request<Source[]>("/sources"),

  uploadFile: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<{ id: string }>("/sources/files", { method: "POST", body: data });
  },

  addGithub: (url: string, includeIssues: boolean) =>
    request<{ id: string }>("/sources/github", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, include_issues: includeIssues }),
    }),

  createResearch: (question: string, mode: RunMode) =>
    request<ResearchRun>("/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode, top_k: 6 }),
    }),

  getResearch: (id: string) => request<ResearchRun>(`/research/${id}`),

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
    ];
    names.forEach((name) =>
      stream.addEventListener(name, (raw) => {
        const event = JSON.parse((raw as MessageEvent).data) as TraceEvent;
        onEvent(event);
        if (name === "run_completed" || name === "run_failed") {
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
