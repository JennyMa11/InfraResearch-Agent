import { expect, test } from "@playwright/test";

test.skip(!process.env.DEMO_CAPTURE, "run through scripts/record-demo.sh");

test("records the explainable Agent workflow", async ({ page }) => {
  test.setTimeout(75_000);
  const timestamp = new Date().toISOString();
  const source = {
    id: "src-demo",
    kind: "file",
    name: "prefix-cache-guide.md",
    uri: "prefix-cache-guide.md",
    status: "completed",
    revision: null,
    error: null,
    metadata: { embedding_model: "intfloat/multilingual-e5-small" },
    created_at: timestamp,
  };
  let imported = false;
  const pageOf = <T,>(items: T[]) => ({
    items,
    page: 1,
    page_size: 10,
    total: items.length,
    pages: items.length ? 1 : 0,
  });
  const events = [
    { sequence: 1, event_type: "run_started", node: null, data: {}, created_at: timestamp },
    {
      sequence: 2,
      event_type: "plan_created",
      node: "planner",
      data: { plan: { subquestions: [{ question: "Prefix caching 如何减少 prefill？" }] } },
      created_at: timestamp,
    },
    {
      sequence: 3,
      event_type: "tool_started",
      node: "retriever",
      data: { tool: "semantic_document_search", query: "prefix caching", top_k: 20 },
      created_at: timestamp,
    },
    {
      sequence: 4,
      event_type: "observation_created",
      node: "retriever",
      data: { results: 2, top_score: 0.31, status: "completed" },
      created_at: timestamp,
    },
    {
      sequence: 5,
      event_type: "evidence_graded",
      node: "evidence_grader",
      data: { coverage: 0.25, relevance: 0.31, diversity: 0.5, sufficient: false },
      created_at: timestamp,
    },
    {
      sequence: 6,
      event_type: "decision_made",
      node: "evidence_grader",
      data: {
        action: "retrieve_again",
        reason: "coverage=0.25，低于阈值0.35；需要补充实现证据",
      },
      created_at: timestamp,
    },
    {
      sequence: 7,
      event_type: "query_rewritten",
      node: "evidence_grader",
      data: {
        previous_queries: ["prefix caching"],
        queries: ["prefix caching KV block prefill implementation"],
      },
      created_at: timestamp,
    },
    {
      sequence: 8,
      event_type: "tool_started",
      node: "retriever",
      data: {
        tool: "code_keyword_search",
        query: "prefix caching KV block prefill implementation",
        top_k: 20,
      },
      created_at: timestamp,
    },
    {
      sequence: 9,
      event_type: "observation_created",
      node: "retriever",
      data: { results: 6, top_score: 0.88, status: "completed" },
      created_at: timestamp,
    },
    {
      sequence: 10,
      event_type: "rerank_completed",
      node: "reranker",
      data: {
        candidates: 8,
        status: "completed",
        reranker: "fastembed:BAAI/bge-reranker-base",
        ranking: [
          {
            locator: "prefix-cache-guide.md#L12-L20",
            retrieval_rank: 3,
            rerank_rank: 1,
          },
        ],
      },
      created_at: timestamp,
    },
    {
      sequence: 11,
      event_type: "evidence_graded",
      node: "evidence_grader",
      data: { coverage: 0.8, relevance: 0.91, diversity: 1, sufficient: true },
      created_at: timestamp,
    },
    {
      sequence: 12,
      event_type: "decision_made",
      node: "evidence_grader",
      data: { action: "generate", reason: "证据覆盖度和相关性均达到阈值" },
      created_at: timestamp,
    },
    {
      sequence: 13,
      event_type: "node_completed",
      node: "citation_verifier",
      data: { citations: 1, invalid: [], repaired: false },
      created_at: timestamp,
    },
    { sequence: 14, event_type: "run_completed", node: null, data: {}, created_at: timestamp },
  ];
  const metrics = {
    total_latency_ms: 684,
    ttft_ms: 92,
    prompt_tokens: 612,
    completion_tokens: 126,
    tool_calls: 2,
    agent_steps: 7,
    retrieval_rounds: 2,
    prefix_cache_hits: null,
    prefix_cache_queries: null,
    kv_cache_usage: null,
    provider: "openai_compatible",
    vector_backend: "qdrant_local",
    reranker: "fastembed:BAAI/bge-reranker-base",
    reranker_status: "completed",
    reranker_latency_ms: 188,
    candidate_k: 20,
    evidence_k: 6,
  };
  const pendingRun = {
    id: "run-demo",
    question: "Prefix caching 如何减少重复 prefill？",
    mode: "agentic",
    status: "pending",
    answer: null,
    plan: { question_type: "technical", subquestions: [] },
    metrics: { ...metrics, provider: "unknown" },
    error: null,
    evidence: [],
    citations: [],
    events: [],
    tool_calls: [],
    created_at: timestamp,
    completed_at: null,
  };
  const completedRun = {
    ...pendingRun,
    status: "completed",
    answer:
      "Prefix caching 复用相同提示前缀已经计算好的 KV cache block，" +
      "从而跳过重复的 prefill 计算，同时不会改变后续采样结果 [S1]。",
    plan: {
      question_type: "technical",
      subquestions: [
        { question: "Prefix caching 如何减少 prefill？", expected_evidence: "docs" },
      ],
    },
    metrics,
    evidence: [
      {
        id: "S1",
        chunk_id: "chunk-demo",
        source_id: "src-demo",
        content:
          "Prefix caching reuses KV cache blocks for requests sharing the same prompt prefix. " +
          "It avoids repeated prefill computation without changing sampling semantics.",
        locator: "prefix-cache-guide.md#L12-L20",
        score: 0.94,
        retrieval_score: 0.72,
        rerank_score: 0.94,
        metadata: { category: "docs" },
      },
    ],
    citations: [
      {
        id: "citation-demo",
        evidence_id: "S1",
        marker: "[S1]",
        claim: "Prefix caching avoids repeated prefill",
        valid: true,
      },
    ],
    events,
    tool_calls: [
      {
        id: "tool-docs",
        name: "semantic_document_search",
        arguments: { query: "prefix caching", top_k: 20 },
        result_summary: "2 results",
        duration_ms: 22,
        status: "completed",
        error: null,
      },
      {
        id: "tool-code",
        name: "code_keyword_search",
        arguments: { query: "prefix caching KV block prefill implementation", top_k: 20 },
        result_summary: "6 results",
        duration_ms: 18,
        status: "completed",
        error: null,
      },
    ],
    completed_at: timestamp,
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/sources" && request.method() === "GET") {
      await route.fulfill({ json: pageOf(imported ? [source] : []) });
      return;
    }
    if (path === "/api/v1/sources/files" && request.method() === "POST") {
      imported = true;
      await route.fulfill({ status: 202, json: { id: "ing-demo" } });
      return;
    }
    if (path === "/api/v1/research" && request.method() === "GET") {
      await route.fulfill({ json: pageOf([]) });
      return;
    }
    if (path === "/api/v1/research" && request.method() === "POST") {
      await route.fulfill({ status: 202, json: pendingRun });
      return;
    }
    if (path === "/api/v1/research/run-demo/events") {
      const body = events
        .map(
          (event) =>
            `id: ${event.sequence}\nevent: ${event.event_type}\ndata: ${JSON.stringify(event)}\n\n`,
        )
        .join("");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
      return;
    }
    if (path === "/api/v1/research/run-demo") {
      await route.fulfill({ json: completedRun });
      return;
    }
    await route.abort();
  });

  await page.goto("/");
  await page.waitForTimeout(3000);
  await page.setInputFiles("#file", {
    name: "prefix-cache-guide.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Prefix caching\n\nKV block reuse and prefill."),
  });
  await expect(page.getByText("prefix-cache-guide.md", { exact: true })).toBeVisible();
  await page.waitForTimeout(4000);

  await page
    .getByLabel("研究问题")
    .fill("Prefix caching 如何减少重复 prefill？");
  await page.waitForTimeout(2000);
  await page.getByRole("button", { name: /开始研究/ }).click();
  await expect(page.getByLabel("直接输出结果")).toHaveValue(/KV cache block/);
  await page.waitForTimeout(7000);

  await expect(page.getByText("Agent 决策", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("查询重写", { exact: true })).toBeVisible();
  await expect(page.getByText("候选精排", { exact: true })).toBeVisible();
  await page.getByText("Agent 路径", { exact: true }).scrollIntoViewIfNeeded();
  await page.waitForTimeout(7000);

  await page.getByRole("button", { name: "[S1]" }).click();
  await expect(page.getByLabel("证据 S1")).toContainText("prefix-cache-guide.md#L12-L20");
  await page.waitForTimeout(7000);
});
