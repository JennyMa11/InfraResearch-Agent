import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  EvidenceDrawer,
  Report,
  ResearchHistory,
  SourcesPanel,
  Timeline,
} from "./components";
import type { ResearchRun } from "./types";

describe("source and trace views", () => {
  it("shows source status and backend error", () => {
    render(
      <SourcesPanel
        sources={[
          {
            id: "src_1",
            kind: "file",
            name: "manual.md",
            uri: "manual.md",
            status: "failed",
            revision: null,
            error: "unsupported encoding",
            metadata: {},
            created_at: new Date().toISOString(),
          },
        ]}
      />,
    );
    expect(screen.getByText("manual.md")).toBeInTheDocument();
    expect(screen.getByText("unsupported encoding")).toBeInTheDocument();
  });

  it("offers source reindex and delete actions", () => {
    const onDelete = vi.fn();
    const onReindex = vi.fn();
    render(
      <SourcesPanel
        sources={[
          {
            id: "src_1",
            kind: "file",
            name: "manual.md",
            uri: "manual.md",
            status: "completed",
            revision: null,
            error: null,
            metadata: {},
            created_at: new Date().toISOString(),
          },
        ]}
        onDelete={onDelete}
        onReindex={onReindex}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "重建 manual.md" }));
    fireEvent.click(screen.getByRole("button", { name: "删除 manual.md" }));
    expect(onReindex).toHaveBeenCalledWith(expect.objectContaining({ id: "src_1" }));
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: "src_1" }));
  });

  it("filters, pages and cancels an active source", () => {
    const onCancel = vi.fn();
    const onQueryChange = vi.fn();
    const onPageChange = vi.fn();
    const source = {
      id: "src_active",
      kind: "file",
      name: "active.md",
      uri: "active.md",
      status: "running",
      revision: null,
      error: null,
      metadata: {},
      active_ingestion_id: "ing_active",
      created_at: new Date().toISOString(),
    };
    render(
      <SourcesPanel
        sources={[source]}
        query=""
        statusFilter=""
        page={1}
        pages={2}
        total={11}
        onCancel={onCancel}
        onQueryChange={onQueryChange}
        onStatusChange={() => undefined}
        onPageChange={onPageChange}
      />,
    );
    fireEvent.change(screen.getByLabelText("搜索数据源"), {
      target: { value: "active" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消 active.md" }));
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(onQueryChange).toHaveBeenCalledWith("active");
    expect(onCancel).toHaveBeenCalledWith(source);
    expect(onPageChange).toHaveBeenCalledWith(2);
    expect(screen.getByText("11")).toBeInTheDocument();
  });

  it("renders an ordered live timeline", () => {
    render(
      <Timeline
        events={[
          {
            sequence: 1,
            event_type: "run_started",
            node: null,
            data: {},
            created_at: new Date().toISOString(),
          },
          {
            sequence: 2,
            event_type: "token",
            node: "generator",
            data: { text: "private generation chunk" },
            created_at: new Date().toISOString(),
          },
          {
            sequence: 3,
            event_type: "tool_completed",
            node: "retriever",
            data: { duration_ms: 12 },
            created_at: new Date().toISOString(),
          },
        ]}
      />,
    );
    expect(screen.getByText("运行开始")).toBeInTheDocument();
    expect(screen.getByText("12 ms")).toBeInTheDocument();
    expect(screen.getByText("已隐藏 1 个生成 token")).toBeInTheDocument();
    expect(screen.queryByText("token")).not.toBeInTheDocument();
  });
});

describe("research history", () => {
  it("restores a run and offers retry for failures", () => {
    const onOpen = vi.fn();
    const onRetry = vi.fn();
    const failed = {
      id: "run_1",
      question: "Why did the worker stop?",
      mode: "agentic" as const,
      status: "failed",
      provider: "unknown",
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    };
    render(
      <ResearchHistory
        runs={[failed]}
        onOpen={onOpen}
        onRetry={onRetry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Why did the worker stop/ }));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onOpen).toHaveBeenCalledWith(failed);
    expect(onRetry).toHaveBeenCalledWith(failed);
  });

  it("filters and cancels active research", () => {
    const onCancel = vi.fn();
    const active = {
      id: "run_active",
      question: "Long-running research",
      mode: "agentic" as const,
      status: "running",
      provider: "unknown",
      created_at: new Date().toISOString(),
      completed_at: null,
    };
    render(
      <ResearchHistory
        runs={[active]}
        onOpen={() => undefined}
        onRetry={() => undefined}
        onCancel={onCancel}
        query=""
        statusFilter=""
        page={1}
        pages={1}
        total={1}
        onQueryChange={() => undefined}
        onStatusChange={() => undefined}
        onPageChange={() => undefined}
      />,
    );
    fireEvent.change(screen.getByLabelText("研究状态"), {
      target: { value: "running" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledWith(active);
  });
});

describe("citations", () => {
  it("opens a registered evidence marker", () => {
    const onCitation = vi.fn();
    const run = {
      answer:
        "<think>private reasoning must stay hidden</think>\n\n" +
        "Prefix cache reuses KV blocks [S1].",
    } as ResearchRun;
    render(<Report run={run} onCitation={onCitation} />);
    expect(screen.getByLabelText("直接输出结果")).toHaveValue(
      "Prefix cache reuses KV blocks [S1].",
    );
    expect(screen.queryByText(/private reasoning/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "[S1]" }));
    expect(onCitation).toHaveBeenCalledWith("S1");
  });

  it("hides duplicate markers from an existing stored report", () => {
    const duplicate = {
      chunk_id: "c1",
      source_id: "s1",
      content: "Prefix cache reuses KV blocks.",
      locator: "demo.md#L1-L2",
      score: 0.9,
      metadata: {},
    };
    const run = {
      answer: "Prefix cache reuses KV blocks. [S1] [S2] [S3]",
      evidence: [
        { ...duplicate, id: "S1" },
        { ...duplicate, id: "S2", chunk_id: "c2" },
        { ...duplicate, id: "S3", chunk_id: "c3" },
      ],
    } as ResearchRun;
    const view = render(<Report run={run} onCitation={() => undefined} />);
    expect(view.container.querySelector("textarea")).toHaveValue(
      "Prefix cache reuses KV blocks. [S1]",
    );
    expect(view.container.querySelectorAll("button.citation")).toHaveLength(1);
  });

  it("shows locator and content in evidence drawer", () => {
    render(
      <EvidenceDrawer
        evidence={{
          id: "S1",
          chunk_id: "c1",
          source_id: "s1",
          content: "Prefix cache content",
          locator: "repo@sha/docs/cache.md#L1-L10",
          score: 0.9,
          retrieval_score: 0.8,
          rerank_score: 0.9,
          metadata: {},
        }}
        onClose={() => undefined}
      />,
    );
    expect(screen.getByText("repo@sha/docs/cache.md#L1-L10")).toBeInTheDocument();
    expect(screen.getByText("Prefix cache content")).toBeInTheDocument();
  });
});
