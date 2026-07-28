import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EvidenceDrawer, Report, SourcesPanel, Timeline } from "./components";
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
  });
});

describe("citations", () => {
  it("opens a registered evidence marker", () => {
    const onCitation = vi.fn();
    const run = {
      answer: "Prefix cache reuses KV blocks [S1].",
    } as ResearchRun;
    render(<Report run={run} onCitation={onCitation} />);
    fireEvent.click(screen.getByRole("button", { name: "[S1]" }));
    expect(onCitation).toHaveBeenCalledWith("S1");
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
          metadata: {},
        }}
        onClose={() => undefined}
      />,
    );
    expect(screen.getByText("repo@sha/docs/cache.md#L1-L10")).toBeInTheDocument();
    expect(screen.getByText("Prefix cache content")).toBeInTheDocument();
  });
});
