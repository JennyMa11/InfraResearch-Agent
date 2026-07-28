from pathlib import Path

from infraresearch.chunking import chunk_markdown, chunk_path, chunk_pdf, is_indexable


def test_markdown_chunks_keep_heading_and_stable_lines() -> None:
    chunks = chunk_markdown(
        "# Install\n\nUse uv sync.\n\n## Run\n\nUse uvicorn.", "guide.md"
    )
    assert [chunk.heading for chunk in chunks] == ["Install", "Run"]
    assert chunks[0].locator == "guide.md#L1-L4"
    assert chunks[1].locator == "guide.md#L5-L7"


def test_code_windows_keep_repository_locator(tmp_path: Path) -> None:
    code = tmp_path / "server.py"
    code.write_text("\n".join(f"line_{index} = {index}" for index in range(40)))
    chunks = chunk_path(
        code,
        "src/server.py",
        size=800,
        overlap=80,
        locator_prefix="owner/repo@abc123/",
    )
    assert chunks
    assert chunks[0].locator.startswith("owner/repo@abc123/src/server.py#L1-L")
    assert chunks[0].content_hash == chunks[0].content_hash


def test_secret_and_weight_files_are_ignored() -> None:
    assert not is_indexable(Path(".env"))
    assert not is_indexable(Path("models/model.safetensors"))
    assert not is_indexable(Path("node_modules/pkg/index.ts"))
    assert is_indexable(Path("src/kernel.cu"))


def test_pdf_chunks_keep_one_based_page_locator(monkeypatch, tmp_path: Path) -> None:
    class Page:
        def __init__(self, text: str):
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        def __init__(self, _path: str):
            self.pages = [Page("First page evidence."), Page("Second page evidence.")]

    monkeypatch.setattr("infraresearch.chunking.PdfReader", Reader)
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-mocked")
    chunks = chunk_pdf(pdf, "manual.pdf")
    assert [chunk.page for chunk in chunks] == [1, 2]
    assert [chunk.locator for chunk in chunks] == [
        "manual.pdf#page=1",
        "manual.pdf#page=2",
    ]
