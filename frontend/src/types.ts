export type RunMode = "naive" | "agentic";

export interface Source {
  id: string;
  kind: string;
  name: string;
  uri: string;
  status: string;
  revision: string | null;
  error: string | null;
  metadata: Record<string, unknown>;
  active_ingestion_id?: string | null;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface Ingestion {
  id: string;
  source_id: string;
  status: string;
  chunks_indexed: number;
  files_seen: number;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ResearchRunSummary {
  id: string;
  question: string;
  mode: RunMode;
  status: string;
  provider: string;
  created_at: string;
  completed_at: string | null;
}

export interface Evidence {
  id: string;
  chunk_id: string;
  source_id: string;
  content: string;
  locator: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface TraceEvent {
  sequence: number;
  event_type: string;
  node: string | null;
  data: Record<string, unknown>;
  created_at: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result_summary: string;
  duration_ms: number;
  status: string;
  error: string | null;
}

export interface RunMetrics {
  total_latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  tool_calls: number;
  agent_steps: number;
  retrieval_rounds: number;
  prefix_cache_hits: number | null;
  prefix_cache_queries: number | null;
  kv_cache_usage: number | null;
  provider: string;
  vector_backend: string;
}

export interface ResearchRun {
  id: string;
  question: string;
  mode: RunMode;
  status: string;
  answer: string | null;
  plan: {
    question_type: string;
    subquestions: Array<{ question: string; expected_evidence: string }>;
  };
  metrics: RunMetrics;
  error: string | null;
  evidence: Evidence[];
  citations: Array<{
    id: string;
    evidence_id: string;
    marker: string;
    claim: string;
    valid: boolean;
  }>;
  events: TraceEvent[];
  tool_calls: ToolCall[];
  created_at: string;
  completed_at: string | null;
}
