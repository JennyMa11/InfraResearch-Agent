#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx


class AcceptanceFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def wait_for_resource(
    client: httpx.Client,
    path: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(path)
        response.raise_for_status()
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.1)
    raise AcceptanceFailure(f"{path} did not finish within {timeout_seconds}s; last={last}")


def upload(
    client: httpx.Client,
    name: str,
    content: bytes,
    media_type: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/sources/files",
        files={"file": (name, content, media_type)},
    )
    require(response.status_code == 202, f"{name} upload failed: {response.text}")
    ingestion_id = response.json()["id"]
    result = wait_for_resource(
        client,
        f"/api/v1/ingestions/{ingestion_id}",
        timeout_seconds=30,
    )
    require(result["status"] == "completed", f"{name} ingestion failed: {result}")
    require(result["chunks_indexed"] > 0, f"{name} created no chunks")
    require(result["files_seen"] == 1, f"{name} files_seen was not 1")
    return result


def make_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode())
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


def research(
    client: httpx.Client,
    question: str,
    mode: str,
    *,
    timeout_seconds: float = 45,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/research",
        json={"question": question, "mode": mode, "top_k": 6},
    )
    require(response.status_code == 202, f"{mode} research creation failed: {response.text}")
    run_id = response.json()["id"]
    result = wait_for_resource(
        client,
        f"/api/v1/research/{run_id}",
        timeout_seconds=timeout_seconds,
    )
    require(result["status"] == "completed", f"{mode} research failed: {result}")
    require(result["events"], f"{mode} research emitted no events")
    sequences = [event["sequence"] for event in result["events"]]
    require(sequences == list(range(1, len(sequences) + 1)), "trace sequence is not contiguous")
    require(result["events"][0]["event_type"] == "run_started", "trace has no run_started")
    require(result["events"][-1]["event_type"] == "run_completed", "trace has no run_completed")
    require(result["completed_at"] is not None, "completed research has no completed_at")
    return result


def validate_evidence_run(result: dict[str, Any], expected_provider: str) -> None:
    require(result["answer"], "evidence run has no answer")
    require(result["plan"]["subquestions"], "evidence run has no research plan")
    require(result["evidence"], "evidence run has no registered evidence")
    require(result["tool_calls"], "evidence run has no tool calls")
    require(result["metrics"]["provider"] == expected_provider, "unexpected provider")
    require(result["metrics"]["retrieval_rounds"] >= 1, "retrieval was not recorded")
    require(result["metrics"]["tool_calls"] == len(result["tool_calls"]), "tool metric mismatch")
    require(result["metrics"]["total_latency_ms"] > 0, "latency was not recorded")

    evidence_ids = {item["id"] for item in result["evidence"]}
    require(
        all(item["evidence_id"] in evidence_ids for item in result["citations"]),
        "citation points to unregistered evidence",
    )
    require(all(item["valid"] for item in result["citations"]), "invalid citation persisted")
    for item in result["evidence"]:
        require(item["locator"], "evidence locator is empty")
        require(item["score"] >= 0, "evidence score is negative")


def validate_sse(client: httpx.Client, run_id: str) -> None:
    response = client.get(f"/api/v1/research/{run_id}/events", timeout=30)
    response.raise_for_status()
    require(
        response.headers["content-type"].startswith("text/event-stream"),
        "events endpoint is not SSE",
    )
    require("event: run_started" in response.text, "SSE omitted run_started")
    require("event: run_completed" in response.text, "SSE omitted run_completed")
    ids = [
        int(line.removeprefix("id: ").strip())
        for line in response.text.splitlines()
        if line.startswith("id: ")
    ]
    require(ids == sorted(ids) and len(ids) == len(set(ids)), "SSE ids are not ordered and unique")


def validate_contract(client: httpx.Client, expected_vector_backend: str | None) -> None:
    response = client.get("/api/v1/health")
    response.raise_for_status()
    health = response.json()
    require(health["status"] == "ok", "health status is not ok")
    require(health["version"], "health version is empty")
    if expected_vector_backend:
        require(
            health["vector_backend"] == expected_vector_backend,
            f"expected {expected_vector_backend}, got {health['vector_backend']}",
        )

    response = client.get("/openapi.json")
    response.raise_for_status()
    paths = response.json()["paths"]
    expected_paths = {
        "/api/v1/health",
        "/api/v1/sources/files",
        "/api/v1/sources/github",
        "/api/v1/sources",
        "/api/v1/sources/{source_id}/reindex",
        "/api/v1/sources/{source_id}",
        "/api/v1/ingestions/{ingestion_id}",
        "/api/v1/ingestions/{ingestion_id}/cancel",
        "/api/v1/research",
        "/api/v1/research/{run_id}/retry",
        "/api/v1/research/{run_id}/cancel",
        "/api/v1/research/{run_id}",
        "/api/v1/research/{run_id}/events",
        "/api/v1/metrics/summary",
    }
    require(expected_paths <= set(paths), "OpenAPI schema is missing public routes")

    require(client.get("/api/v1/ingestions/missing").status_code == 404, "missing ingestion")
    require(client.get("/api/v1/research/missing").status_code == 404, "missing research")
    require(
        client.get("/api/v1/research/missing/events").status_code == 404,
        "missing SSE research",
    )

    response = client.post(
        "/api/v1/sources/files",
        files={"file": ("weights.safetensors", b"binary", "application/octet-stream")},
    )
    require(response.status_code == 415, "unsupported upload did not return 415")

    response = client.post(
        "/api/v1/sources/github",
        json={"url": "https://gitlab.com/example/repository", "include_issues": False},
    )
    require(response.status_code == 422, "non-GitHub URL did not return 422")

    response = client.post(
        "/api/v1/research",
        json={"question": "x", "mode": "agentic", "top_k": 6},
    )
    require(response.status_code == 422, "short question did not return 422")

    response = client.post(
        "/api/v1/research",
        json={"question": "valid question", "mode": "agentic", "top_k": 21},
    )
    require(response.status_code == 422, "out-of-range top_k did not return 422")


def validate_github(
    client: httpx.Client, url: str, *, include_issues: bool
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/sources/github",
        json={"url": url, "include_issues": include_issues},
    )
    require(response.status_code == 202, f"GitHub import creation failed: {response.text}")
    result = wait_for_resource(
        client,
        f"/api/v1/ingestions/{response.json()['id']}",
        timeout_seconds=240,
    )
    require(result["status"] == "completed", f"GitHub import failed: {result}")
    require(result["files_seen"] > 0, "GitHub import saw no files")
    require(result["chunks_indexed"] > 0, "GitHub import indexed no chunks")

    sources = client.get("/api/v1/sources").json()["items"]
    repositories = [item for item in sources if item["kind"] == "github" and item["uri"] == url]
    require(repositories, "GitHub source is missing from source list")
    source = repositories[0]
    require(source["status"] == "completed", "GitHub source is not completed")
    require(source["revision"], "GitHub source has no pinned revision")
    require(source["metadata"].get("owner"), "GitHub source metadata has no owner")
    require(source["metadata"].get("repo"), "GitHub source metadata has no repo")
    if include_issues:
        require(
            isinstance(source["metadata"].get("issues_indexed"), int),
            "GitHub Issues request did not persist an issue count",
        )
    return {
        "source": source["name"],
        "revision": source["revision"],
        "files_seen": result["files_seen"],
        "chunks_indexed": result["chunks_indexed"],
        "issues_indexed": source["metadata"].get("issues_indexed"),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=15, trust_env=False) as client:
        validate_contract(client, args.expected_vector_backend)

        markdown_ingestion = upload(
            client,
            "acceptance-cache.md",
            (
                b"# Prefix cache\n\n"
                b"Prefix caching reuses KV blocks for requests sharing the same prompt prefix.\n"
                b"It does not change model weights or the mathematical output for the same inputs.\n"
            ),
            "text/markdown",
        )
        code_ingestion = upload(
            client,
            "acceptance_cache.py",
            (
                b"def allocate_kv_cache(blocks: int) -> list[int]:\n"
                b"    \"\"\"Reuse allocated KV cache blocks for matching prompt prefixes.\"\"\"\n"
                b"    return list(range(blocks))\n"
            ),
            "text/x-python",
        )
        pdf_ingestion = upload(
            client,
            "acceptance-manual.pdf",
            make_pdf("PDF evidence says prefix caching reuses KV cache blocks."),
            "application/pdf",
        )

        reindex_response = client.post(
            f"/api/v1/sources/{markdown_ingestion['source_id']}/reindex"
        )
        require(reindex_response.status_code == 202, "source reindex was not accepted")
        reindexed = wait_for_resource(
            client,
            f"/api/v1/ingestions/{reindex_response.json()['id']}",
            timeout_seconds=30,
        )
        require(reindexed["status"] == "completed", f"source reindex failed: {reindexed}")
        require(
            reindexed["chunks_indexed"] == markdown_ingestion["chunks_indexed"],
            "source reindex changed the stable chunk count",
        )

        sources = client.get("/api/v1/sources")
        sources.raise_for_status()
        source_page = sources.json()
        source_items = source_page["items"]
        require(source_page["total"] == 3, "source pagination total mismatch")
        require(len(source_items) == 3, f"expected three isolated sources, got {len(source_items)}")
        require(all(item["status"] == "completed" for item in source_items), "source failed")
        require(
            all(item["metadata"].get("embedding_model") for item in source_items),
            "source metadata was not persisted",
        )

        naive = research(
            client,
            "How does prefix caching reuse KV blocks for the same prompt prefix?",
            "naive",
        )
        validate_evidence_run(naive, args.expected_provider)
        require(naive["metrics"]["retrieval_rounds"] == 1, "naive mode retrieved more than once")
        require(
            {item["name"] for item in naive["tool_calls"]} == {"semantic_document_search"},
            "naive mode used unexpected tools",
        )
        validate_sse(client, naive["id"])

        agentic = research(
            client,
            "How does the allocate_kv_cache code reuse blocks?",
            "agentic",
        )
        validate_evidence_run(agentic, args.expected_provider)
        tool_names = {item["name"] for item in agentic["tool_calls"]}
        require("semantic_document_search" in tool_names, "agentic semantic tool was not used")
        require("code_keyword_search" in tool_names, "agentic code tool was not used")

        chitchat = research(client, "你好", "agentic")
        require(chitchat["metrics"]["provider"] == "rule_based", "chitchat did not use router")
        require(not chitchat["tool_calls"], "chitchat unexpectedly used tools")
        require(not chitchat["evidence"], "chitchat unexpectedly registered evidence")

        summary_response = client.get("/api/v1/metrics/summary")
        summary_response.raise_for_status()
        summary = summary_response.json()
        require(summary["run_count"] == 3, "metrics run_count mismatch")
        require(summary["completed_count"] == 3, "metrics completed_count mismatch")
        require(summary["p50_latency_ms"] is not None, "metrics p50 is empty")
        require(summary["p95_latency_ms"] is not None, "metrics p95 is empty")
        require(summary["total_tool_calls"] == len(naive["tool_calls"]) + len(agentic["tool_calls"]), "metrics tool count mismatch")

        history_response = client.get("/api/v1/research")
        history_response.raise_for_status()
        history_page = history_response.json()
        history = history_page["items"]
        require(history_page["total"] == 3, "research pagination total mismatch")
        require(len(history) == 3, "research history did not return all runs")
        require(history[0]["id"] == chitchat["id"], "research history is not newest first")

        retry_response = client.post(f"/api/v1/research/{naive['id']}/retry")
        require(retry_response.status_code == 202, "research retry was not accepted")
        retried = wait_for_resource(
            client,
            f"/api/v1/research/{retry_response.json()['id']}",
            timeout_seconds=45,
        )
        require(retried["status"] == "completed", f"research retry failed: {retried}")
        summary = client.get("/api/v1/metrics/summary").json()
        require(summary["run_count"] == 4, "retry was not included in metrics")
        require(summary["completed_count"] == 4, "retried run was not completed in metrics")

        delete_response = client.delete(
            f"/api/v1/sources/{code_ingestion['source_id']}"
        )
        require(delete_response.status_code == 204, "source deletion failed")
        remaining_sources = client.get("/api/v1/sources").json()["items"]
        require(
            all(item["id"] != code_ingestion["source_id"] for item in remaining_sources),
            "deleted source remained visible",
        )
        preserved = client.get(f"/api/v1/research/{agentic['id']}").json()
        require(preserved["evidence"], "source deletion removed historical evidence")

        result: dict[str, Any] = {
            "health": "ok",
            "sources": len(source_items),
            "ingestions": {
                "markdown_chunks": markdown_ingestion["chunks_indexed"],
                "code_chunks": code_ingestion["chunks_indexed"],
                "pdf_chunks": pdf_ingestion["chunks_indexed"],
            },
            "runs": {
                "naive": naive["id"],
                "agentic": agentic["id"],
                "chitchat": chitchat["id"],
                "retried": retried["id"],
            },
            "lifecycle": {
                "reindexed_source": markdown_ingestion["source_id"],
                "deleted_source": code_ingestion["source_id"],
                "history_count": len(history) + 1,
            },
            "provider": args.expected_provider,
            "vector_backend": naive["metrics"]["vector_backend"],
            "metrics": summary,
        }
        if args.github_url:
            result["github"] = validate_github(
                client,
                args.github_url.rstrip("/"),
                include_issues=args.github_issues,
            )
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the public API over real HTTP.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--expected-provider", default="extractive")
    parser.add_argument("--expected-vector-backend")
    parser.add_argument("--github-url")
    parser.add_argument("--github-issues", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args)
    except (AcceptanceFailure, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"API acceptance failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
