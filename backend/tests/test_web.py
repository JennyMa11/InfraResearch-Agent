import json

import httpx
import pytest

from infraresearch.config import Settings
from infraresearch.models import Source
from infraresearch.retrieval import VectorIndex
from infraresearch.tooling import create_tool_registry
from infraresearch.web import _ReadableHTML, validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "https://localhost/private",
    ],
)
def test_private_and_non_http_urls_are_rejected(url) -> None:
    with pytest.raises(ValueError):
        validate_public_url(url, resolve=False)


def test_html_reader_keeps_headings_and_ignores_scripts() -> None:
    parser = _ReadableHTML()
    parser.feed(
        "<html><head><title>Guide</title><script>secret()</script></head>"
        "<body><h1>Cache</h1><p>Reuse KV blocks.</p></body></html>"
    )
    assert parser.title == "Guide"
    assert "# Cache" in parser.markdown()
    assert "Reuse KV blocks" in parser.markdown()
    assert "secret" not in parser.markdown()


def test_opt_in_web_search_registers_citable_results(session, tmp_path, monkeypatch) -> None:
    request = httpx.Request("GET", "https://search.example/api")
    response = httpx.Response(
        200,
        request=request,
        json={
            "web": {
                "results": [
                    {
                        "title": "Prefix Cache Guide",
                        "url": "https://docs.example.com/cache",
                        "description": "Prefix caching reuses KV blocks.",
                    }
                ]
            }
        },
    )
    monkeypatch.setattr("infraresearch.tooling.httpx.get", lambda *a, **k: response)
    settings = Settings(
        data_dir=tmp_path,
        vector_backend="sqlite",
        web_search_enabled=True,
        web_search_endpoint="https://search.example/api",
        web_search_api_key="test-key",
    )
    registry = create_tool_registry(VectorIndex(settings))

    result = registry.execute(
        session,
        "web_search",
        {"query": "prefix caching", "top_k": 3},
    )

    assert result.status == "completed"
    assert result.hits[0].chunk.locator == "https://docs.example.com/cache"
    assert json.loads(result.hits[0].chunk.metadata_json)["category"] == "web"
    assert session.query(Source).filter_by(kind="web_search").count() == 1
