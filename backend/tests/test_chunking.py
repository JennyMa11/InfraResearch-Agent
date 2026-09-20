from pathlib import Path

from infraresearch.chunking import OCRPage, chunk_markdown, chunk_path, chunk_pdf, is_indexable


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


def test_scanned_pdf_page_uses_injected_ocr_backend(monkeypatch, tmp_path: Path) -> None:
    class Page:
        def extract_text(self) -> str:
            return ""

    class Reader:
        def __init__(self, _path: str):
            self.pages = [Page()]

    class OCR:
        name = "fake-ocr"

        def extract_page(
            self, _path: Path, page_number: int, *, dpi: int, language: str
        ) -> str:
            assert (page_number, dpi, language) == (1, 300, "eng+chi_sim")
            return "Recognized evidence from a scanned page."

    monkeypatch.setattr("infraresearch.chunking.PdfReader", Reader)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-mocked")

    chunks = chunk_pdf(
        pdf,
        "scan.pdf",
        ocr_backend=OCR(),
        ocr_dpi=300,
        ocr_language="eng+chi_sim",
    )

    assert [chunk.content for chunk in chunks] == [
        "Recognized evidence from a scanned page."
    ]
    assert chunks[0].extraction_method == "ocr:fake-ocr"


def test_scanned_pdf_page_preserves_real_ocr_bbox_shape(monkeypatch, tmp_path: Path) -> None:
    class Page:
        def extract_text(self) -> str:
            return ""

    class Reader:
        def __init__(self, _path: str):
            self.pages = [Page()]

    class OCR:
        name = "positioned-ocr"

        def extract_page(self, *_args, **_kwargs) -> OCRPage:
            return OCRPage(
                text="Recognized positioned evidence.",
                bbox=(10.0, 20.0, 300.0, 80.0),
            )

    monkeypatch.setattr("infraresearch.chunking.PdfReader", Reader)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-mocked")

    chunks = chunk_pdf(pdf, "scan.pdf", ocr_backend=OCR())

    assert chunks[0].bbox == (10.0, 20.0, 300.0, 80.0)


def test_pdf_layout_restores_two_column_reading_order(monkeypatch, tmp_path: Path) -> None:
    class Box:
        width = 600

    class Page:
        mediabox = Box()

        def extract_text(self, visitor_text=None) -> str:
            if visitor_text:
                for text, x, y in [
                    ("Right bottom paragraph.", 360, 500),
                    ("Left bottom paragraph.", 40, 500),
                    ("Right top paragraph.", 360, 700),
                    ("Left top paragraph.", 40, 700),
                ]:
                    visitor_text(text, None, [1, 0, 0, 1, x, y], None, 10)
            return "Enough extracted text to avoid OCR and exercise positioned layout parsing."

    class Reader:
        def __init__(self, _path: str):
            self.pages = [Page()]

    monkeypatch.setattr("infraresearch.chunking.PdfReader", Reader)
    pdf = tmp_path / "columns.pdf"
    pdf.write_bytes(b"%PDF-mocked")

    chunks = chunk_pdf(pdf, "columns.pdf")

    content = "\n".join(chunk.content for chunk in chunks)
    assert content.index("Left top") < content.index("Left bottom") < content.index("Right top")
    assert chunks[0].bbox is not None
    assert chunks[0].locator.startswith("columns.pdf#page=1&block=")


def test_pdf_layout_labels_titles_tables_and_captions(monkeypatch, tmp_path: Path) -> None:
    class Box:
        width = 600

    class Page:
        mediabox = Box()

        def extract_text(self, visitor_text=None) -> str:
            if visitor_text:
                for text, y in [
                    ("System Architecture", 740),
                    ("Latency P50 10 P95 20", 650),
                    ("Figure 1: Retrieval pipeline", 550),
                ]:
                    visitor_text(text, None, [1, 0, 0, 1, 50, y], None, 12)
            return "System Architecture Latency table and Figure caption with enough text."

    class Reader:
        def __init__(self, _path: str):
            self.pages = [Page()]

    monkeypatch.setattr("infraresearch.chunking.PdfReader", Reader)
    pdf = tmp_path / "layout.pdf"
    pdf.write_bytes(b"%PDF-mocked")

    chunks = chunk_pdf(pdf, "layout.pdf")

    assert [chunk.block_type for chunk in chunks] == ["title", "table", "caption"]
    assert chunks[1].section == "System Architecture"


def test_markdown_and_python_chunks_preserve_structure(tmp_path: Path) -> None:
    markdown = chunk_markdown("# API\nIntro\n## Retry\nDetails", "guide.md")
    assert [chunk.section for chunk in markdown] == ["API", "API > Retry"]

    code = tmp_path / "service.py"
    code.write_text(
        "class Worker:\n    def run(self):\n        return 1\n\n"
        "def health():\n    return 'ok'\n"
    )
    chunks = chunk_path(code, "service.py")
    assert [(chunk.heading, chunk.block_type) for chunk in chunks] == [
        ("Worker", "class"),
        ("Worker.run", "function"),
        ("health", "function"),
    ]
    assert chunks[0].locator == "service.py#L1-L3"
