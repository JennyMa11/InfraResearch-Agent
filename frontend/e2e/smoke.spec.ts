import { expect, test } from "@playwright/test";

const pageOf = <T,>(items: T[]) => ({
  items,
  page: 1,
  page_size: 10,
  total: items.length,
  pages: items.length ? 1 : 0,
});

test("research workspace renders", async ({ page }) => {
  await page.route("**/api/v1/sources?*", (route) =>
    route.fulfill({ json: pageOf([]) }),
  );
  await page.route("**/api/v1/research?*", (route) =>
    route.fulfill({ json: pageOf([]) }),
  );
  await page.goto("/");
  await expect(page.getByText("找到可验证的答案")).toBeVisible();
  await expect(page.getByLabel("研究问题")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent 路径" })).toBeVisible();
});

test("imports file and GitHub sources", async ({ page }) => {
  const sources: Array<Record<string, unknown>> = [];
  let githubPayload: Record<string, unknown> | null = null;
  let reindexed = false;
  let deleted = false;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/sources" && request.method() === "GET") {
      await route.fulfill({ json: pageOf(sources) });
      return;
    }
    if (path === "/api/v1/research" && request.method() === "GET") {
      await route.fulfill({ json: pageOf([]) });
      return;
    }
    if (path === "/api/v1/sources/files" && request.method() === "POST") {
      sources.push({
        id: "src-file",
        kind: "file",
        name: "cache.md",
        uri: "cache.md",
        status: "completed",
        revision: null,
        error: null,
        metadata: { embedding_model: "test-embedding" },
        created_at: new Date().toISOString(),
      });
      await route.fulfill({ status: 202, json: { id: "ing-file" } });
      return;
    }
    if (path === "/api/v1/sources/github" && request.method() === "POST") {
      githubPayload = request.postDataJSON() as Record<string, unknown>;
      sources.push({
        id: "src-github",
        kind: "github",
        name: "octocat/Spoon-Knife",
        uri: "https://github.com/octocat/Spoon-Knife",
        status: "pending",
        revision: null,
        error: null,
        metadata: {},
        created_at: new Date().toISOString(),
      });
      await route.fulfill({ status: 202, json: { id: "ing-github" } });
      return;
    }
    if (path === "/api/v1/sources/src-file/reindex" && request.method() === "POST") {
      reindexed = true;
      await route.fulfill({
        status: 202,
        json: { id: "ing-reindex", source_id: "src-file", status: "pending" },
      });
      return;
    }
    if (path === "/api/v1/sources/src-file" && request.method() === "DELETE") {
      deleted = true;
      sources.splice(
        sources.findIndex((source) => source.id === "src-file"),
        1,
      );
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.abort();
  });

  await page.goto("/");
  await page.setInputFiles("#file", {
    name: "cache.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Cache\n\nPrefix cache reuses blocks."),
  });
  await expect(page.getByRole("status")).toContainText("cache.md 已进入索引队列");
  await expect(page.getByText("cache.md", { exact: true })).toBeVisible();

  await page.getByLabel("GitHub 仓库 URL").fill("https://github.com/octocat/Spoon-Knife");
  await page.getByLabel("同时索引 Issue").check();
  await page.getByRole("button", { name: "连接公开仓库" }).click();
  await expect(page.getByRole("status")).toContainText("仓库已进入索引队列");
  await expect(page.getByText("octocat/Spoon-Knife", { exact: true })).toBeVisible();
  expect(githubPayload).toEqual({
    url: "https://github.com/octocat/Spoon-Knife",
    include_issues: true,
  });

  await page.getByRole("button", { name: "重建 cache.md" }).click();
  await expect(page.getByRole("status")).toContainText("cache.md 已进入重建队列");
  expect(reindexed).toBe(true);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除 cache.md" }).click();
  await expect(page.getByRole("status")).toContainText("cache.md 已从检索索引删除");
  expect(deleted).toBe(true);
  await expect(page.getByText("cache.md", { exact: true })).toHaveCount(0);
});

test("streams a research report and opens cited evidence", async ({ page }) => {
  const timestamp = new Date().toISOString();
  const pendingRun = {
    id: "run-1",
    question: "How does prefix caching work?",
    mode: "naive",
    status: "pending",
    answer: null,
    plan: { question_type: "technical", subquestions: [] },
    metrics: {
      total_latency_ms: 0,
      ttft_ms: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      tool_calls: 0,
      agent_steps: 0,
      retrieval_rounds: 0,
      prefix_cache_hits: null,
      prefix_cache_queries: null,
      kv_cache_usage: null,
      provider: "unknown",
      vector_backend: "unknown",
    },
    error: null,
    evidence: [],
    citations: [],
    events: [],
    tool_calls: [],
    created_at: timestamp,
    completed_at: null,
  };
  const events = [
    {
      sequence: 1,
      event_type: "run_started",
      node: null,
      data: {},
      created_at: timestamp,
    },
    {
      sequence: 2,
      event_type: "run_completed",
      node: null,
      data: {},
      created_at: timestamp,
    },
  ];
  const completedRun = {
    ...pendingRun,
    status: "completed",
    answer: "Prefix caching reuses KV blocks [S1].",
    plan: {
      question_type: "technical",
      subquestions: [
        {
          question: "How does prefix caching work?",
          expected_evidence: "docs",
        },
      ],
    },
    metrics: {
      ...pendingRun.metrics,
      total_latency_ms: 42,
      ttft_ms: 8,
      prompt_tokens: 20,
      completion_tokens: 10,
      tool_calls: 1,
      agent_steps: 4,
      retrieval_rounds: 1,
      provider: "extractive",
      vector_backend: "qdrant_local",
    },
    evidence: [
      {
        id: "S1",
        chunk_id: "chunk-1",
        source_id: "source-1",
        content: "Prefix caching reuses KV blocks.",
        locator: "cache.md#L1-L3",
        score: 0.91,
        metadata: { category: "docs" },
      },
    ],
    citations: [
      {
        id: "citation-1",
        evidence_id: "S1",
        marker: "[S1]",
        claim: "Prefix caching reuses KV blocks",
        valid: true,
      },
    ],
    events,
    tool_calls: [
      {
        id: "tool-1",
        name: "semantic_document_search",
        arguments: { query: "prefix caching", top_k: 6 },
        result_summary: "1 results",
        duration_ms: 3,
        status: "completed",
        error: null,
      },
    ],
    completed_at: timestamp,
  };
  const history = [
    {
      id: "run-1",
      question: pendingRun.question,
      mode: "naive",
      status: "completed",
      provider: "extractive",
      created_at: timestamp,
      completed_at: timestamp,
    },
  ];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/sources") {
      await route.fulfill({ json: pageOf([]) });
      return;
    }
    if (path === "/api/v1/research" && request.method() === "GET") {
      await route.fulfill({ json: pageOf(history) });
      return;
    }
    if (path === "/api/v1/research" && request.method() === "POST") {
      expect(request.postDataJSON()).toEqual({
        question: "How does prefix caching work?",
        mode: "naive",
        top_k: 6,
      });
      await route.fulfill({ status: 202, json: pendingRun });
      return;
    }
    if (path === "/api/v1/research/run-1/events") {
      const body = events
        .map(
          (event) =>
            `id: ${event.sequence}\nevent: ${event.event_type}\ndata: ${JSON.stringify(event)}\n\n`,
        )
        .join("");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body,
      });
      return;
    }
    if (path === "/api/v1/research/run-1" && request.method() === "GET") {
      await route.fulfill({ json: completedRun });
      return;
    }
    await route.abort();
  });

  await page.goto("/");
  await page.getByLabel("研究问题").fill("How does prefix caching work?");
  await page.getByRole("button", { name: "Naive RAG" }).click();
  await page.getByRole("button", { name: /开始研究/ }).click();

  await expect(page.getByLabel("直接输出结果")).toHaveValue(
    "Prefix caching reuses KV blocks [S1].",
  );
  await expect(page.getByText("extractive", { exact: true })).toBeVisible();
  await expect(page.getByText("semantic_document_search", { exact: true })).toBeVisible();
  await expect(page.getByText("运行完成", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "[S1]" }).click();
  await expect(page.getByLabel("证据 S1")).toContainText("cache.md#L1-L3");
  await expect(page.getByLabel("证据 S1")).toContainText("Prefix caching reuses KV blocks.");
  await page.getByRole("button", { name: "关闭" }).click();
  await expect(page.getByLabel("证据 S1")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "研究历史" })).toBeVisible();
  await expect(page.getByRole("button", { name: /How does prefix caching work/ })).toBeVisible();
});

test("restores failed history and retries it", async ({ page }) => {
  const timestamp = new Date().toISOString();
  const failed = {
    id: "run-failed",
    question: "Why did ingestion stop?",
    mode: "agentic",
    status: "failed",
    provider: "unknown",
    created_at: timestamp,
    completed_at: timestamp,
  };
  const failedRun = {
    ...failed,
    answer: null,
    plan: { question_type: "technical", subquestions: [] },
    metrics: {
      total_latency_ms: 1,
      ttft_ms: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      tool_calls: 0,
      agent_steps: 0,
      retrieval_rounds: 0,
      prefix_cache_hits: null,
      prefix_cache_queries: null,
      kv_cache_usage: null,
      provider: "unknown",
      vector_backend: "sqlite_lexical",
    },
    error: "service restarted",
    evidence: [],
    citations: [],
    events: [],
    tool_calls: [],
  };
  const retried = { ...failedRun, id: "run-retry", status: "pending", error: null };
  let retriedRequest = false;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/sources") {
      await route.fulfill({ json: pageOf([]) });
      return;
    }
    if (path === "/api/v1/research" && request.method() === "GET") {
      await route.fulfill({ json: pageOf([failed]) });
      return;
    }
    if (path === "/api/v1/research/run-failed" && request.method() === "GET") {
      await route.fulfill({ json: failedRun });
      return;
    }
    if (path === "/api/v1/research/run-failed/retry" && request.method() === "POST") {
      retriedRequest = true;
      await route.fulfill({ status: 202, json: retried });
      return;
    }
    if (path === "/api/v1/research/run-retry" && request.method() === "GET") {
      await route.fulfill({ json: { ...retried, status: "failed" } });
      return;
    }
    if (path === "/api/v1/research/run-retry/events") {
      await route.fulfill({ status: 503, body: "" });
      return;
    }
    await route.abort();
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Why did ingestion stop/ }).click();
  await expect(page.getByText("service restarted")).toBeVisible();
  await page.getByRole("button", { name: "重试" }).click();
  expect(retriedRequest).toBe(true);
});
