from __future__ import annotations

import hashlib
import re
from ast import AsyncFunctionDef, ClassDef, FunctionDef, parse
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".cu",
    ".sh",
    ".bash",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
}
CODE_EXTENSIONS = TEXT_EXTENSIONS - {".md", ".markdown", ".txt", ".rst"}
IGNORED_PARTS = {
    ".git",
    ".github",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".venv",
    "venv",
    "vendor",
}
IGNORED_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
}
IGNORED_SUFFIXES = {".pem", ".key", ".crt", ".onnx", ".pt", ".pth", ".safetensors", ".bin"}


@dataclass(slots=True)
class ParsedChunk:
    content: str
    locator: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    page: int | None = None
    heading: str | None = None
    section: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    block_type: str = "paragraph"
    reading_order: int | None = None
    extraction_method: str = "text"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


def is_indexable(path: Path) -> bool:
    if any(part in IGNORED_PARTS for part in path.parts):
        return False
    if path.name.lower() in IGNORED_NAMES or path.suffix.lower() in IGNORED_SUFFIXES:
        return False
    return path.suffix.lower() in TEXT_EXTENSIONS | {".pdf"}


def _window_lines(
    text: str,
    display_path: str,
    *,
    size: int,
    overlap: int,
    prefix: str = "",
    heading: str | None = None,
    block_type: str = "paragraph",
) -> list[ParsedChunk]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[ParsedChunk] = []
    start = 0
    while start < len(lines):
        end = min(len(lines), start + max(10, size // 80))
        content = "\n".join(lines[start:end]).strip()
        if content:
            locator = f"{prefix}{display_path}#L{start + 1}-L{end}"
            chunks.append(
                ParsedChunk(
                    content=content,
                    locator=locator,
                    path=display_path,
                    start_line=start + 1,
                    end_line=end,
                    heading=heading,
                    section=heading,
                    block_type=block_type,
                    reading_order=len(chunks),
                )
            )
        if end == len(lines):
            break
        start = max(start + 1, end - max(1, overlap // 80))
    return chunks


def chunk_markdown(
    text: str, display_path: str, *, size: int = 1200, overlap: int = 120, prefix: str = ""
) -> list[ParsedChunk]:
    lines = text.splitlines()
    headings = [index for index, line in enumerate(lines) if re.match(r"^#{1,6}\s+\S", line)]
    if not headings:
        return _window_lines(text, display_path, size=size, overlap=overlap, prefix=prefix)
    boundaries = headings + [len(lines)]
    result: list[ParsedChunk] = []
    hierarchy: list[str] = []
    for idx in range(len(boundaries) - 1):
        start, end = boundaries[idx], boundaries[idx + 1]
        level = len(lines[start]) - len(lines[start].lstrip("#"))
        heading = re.sub(r"^#{1,6}\s+", "", lines[start]).strip()
        hierarchy = hierarchy[: level - 1]
        hierarchy.append(heading)
        section_path = " > ".join(hierarchy)
        section = "\n".join(lines[start:end]).strip()
        if not section:
            continue
        if len(section) <= size * 2:
            result.append(
                ParsedChunk(
                    content=section,
                    locator=f"{prefix}{display_path}#L{start + 1}-L{end}",
                    path=display_path,
                    start_line=start + 1,
                    end_line=end,
                    heading=heading,
                    section=section_path,
                    block_type="section",
                    reading_order=len(result),
                )
            )
        else:
            windows = _window_lines(
                section,
                display_path,
                size=size,
                overlap=overlap,
                prefix=prefix,
                heading=section_path,
                block_type="section",
            )
            for window in windows:
                window.start_line = (window.start_line or 1) + start
                window.end_line = (window.end_line or 1) + start
                window.locator = f"{prefix}{display_path}#L{window.start_line}-L{window.end_line}"
                window.heading = heading
                window.section = section_path
                window.reading_order = len(result)
            result.extend(windows)
    return result


class OCRBackend(Protocol):
    """Small injection point so OCR stays optional and testable."""

    name: str

    def extract_page(
        self, path: Path, page_number: int, *, dpi: int, language: str
    ) -> str | OCRPage: ...


@dataclass(slots=True)
class OCRPage:
    text: str
    bbox: tuple[float, float, float, float] | None = None


class TesseractOCR:
    """OCR a PDF page through optional PyMuPDF and pytesseract dependencies."""

    name = "tesseract"

    def extract_page(
        self, path: Path, page_number: int, *, dpi: int, language: str
    ) -> OCRPage:
        try:
            import pymupdf as fitz
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - exercised only with OCR extra
            raise RuntimeError(
                "PDF OCR requires the 'ocr' extra and a Tesseract installation"
            ) from exc

        with fitz.open(path) as document:
            page = document.load_page(page_number - 1)
            scale = dpi / 72
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            data = pytesseract.image_to_data(
                image,
                lang=language,
                output_type=pytesseract.Output.DICT,
            )
            words: list[str] = []
            boxes: list[tuple[float, float, float, float]] = []
            for index, raw_text in enumerate(data.get("text", [])):
                text = str(raw_text).strip()
                if not text:
                    continue
                left = float(data["left"][index]) / scale
                top = float(data["top"][index]) / scale
                width = float(data["width"][index]) / scale
                height = float(data["height"][index]) / scale
                words.append(text)
                boxes.append((left, top, left + width, top + height))
            bbox = (
                (
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                )
                if boxes
                else None
            )
            return OCRPage(text=" ".join(words), bbox=bbox)


@dataclass(slots=True)
class _PDFBlock:
    text: str
    bbox: tuple[float, float, float, float]


def _extract_pdf_blocks(page: object) -> list[_PDFBlock]:
    """Extract positioned text and restore a deterministic two-column reading order."""

    blocks: list[_PDFBlock] = []

    def visitor(text: str, _cm: object, tm: list[float], _font: object, size: float) -> None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned or len(tm) < 6:
            return
        x, y = float(tm[4]), float(tm[5])
        width = max(size, len(cleaned) * max(size, 1.0) * 0.48)
        blocks.append(_PDFBlock(cleaned, (x, y, x + width, y + max(size, 1.0))))

    try:
        page.extract_text(visitor_text=visitor)
    except (TypeError, AttributeError):
        return []
    if not blocks:
        return []

    # PDF coordinates start at the bottom. A large persistent horizontal gap is
    # treated as a column boundary; otherwise blocks are sorted top-to-bottom.
    page_width = float(getattr(getattr(page, "mediabox", None), "width", 0) or 0)
    midpoint = page_width / 2 if page_width else 0
    left = [block for block in blocks if midpoint and block.bbox[0] < midpoint]
    right = [block for block in blocks if midpoint and block.bbox[0] >= midpoint]
    is_two_column = bool(left and right and len(left) >= 2 and len(right) >= 2)
    def reading_key(block: _PDFBlock) -> tuple[float, float]:
        return (-round(block.bbox[1], 1), block.bbox[0])

    if is_two_column:
        return sorted(left, key=reading_key) + sorted(right, key=reading_key)
    return sorted(blocks, key=reading_key)


def _pdf_block_type(text: str, *, index: int, total: int) -> str:
    normalized = text.strip()
    if re.match(r"^(table|figure|fig\.)\s*\d+", normalized, re.IGNORECASE):
        return "caption"
    if (
        "|" in normalized
        or sum(bool(re.search(r"\d", token)) for token in normalized.split()) >= 3
    ):
        return "table"
    if index < max(3, total // 12) and len(normalized) < 120 and not normalized.endswith("."):
        return "title"
    return "paragraph"


def _merge_pdf_blocks(
    blocks: list[_PDFBlock], display_path: str, page_number: int, size: int
) -> list[ParsedChunk]:
    result: list[ParsedChunk] = []
    current: list[_PDFBlock] = []
    current_type = "paragraph"
    active_section: str | None = None

    def flush() -> None:
        nonlocal active_section
        if not current:
            return
        text = "\n".join(block.text for block in current).strip()
        bbox = (
            min(block.bbox[0] for block in current),
            min(block.bbox[1] for block in current),
            max(block.bbox[2] for block in current),
            max(block.bbox[3] for block in current),
        )
        heading = text if current_type == "title" else None
        if heading:
            active_section = heading
        result.append(
            ParsedChunk(
                content=text,
                locator=f"{display_path}#page={page_number}&block={len(result) + 1}",
                path=display_path,
                page=page_number,
                heading=heading,
                section=heading or active_section,
                bbox=bbox,
                block_type=current_type,
                reading_order=len(result),
                extraction_method="layout",
            )
        )
        current.clear()

    for index, block in enumerate(blocks):
        kind = _pdf_block_type(block.text, index=index, total=len(blocks))
        projected = sum(len(item.text) for item in current) + len(block.text)
        if current and (kind != current_type or projected > size):
            flush()
        current_type = kind
        current.append(block)
    flush()
    return result


def chunk_pdf(
    path: Path,
    display_path: str,
    *,
    size: int = 1200,
    layout: bool = True,
    ocr_enabled: bool = True,
    ocr_min_chars: int = 40,
    ocr_dpi: int = 200,
    ocr_language: str = "eng",
    ocr_backend: OCRBackend | None = None,
) -> list[ParsedChunk]:
    reader = PdfReader(str(path))
    result: list[ParsedChunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        ocr_bbox: tuple[float, float, float, float] | None = None
        if len(text) < ocr_min_chars and ocr_enabled:
            backend = ocr_backend or TesseractOCR()
            try:
                ocr_result = backend.extract_page(
                    path, page_number, dpi=ocr_dpi, language=ocr_language
                )
                if isinstance(ocr_result, OCRPage):
                    ocr_text = ocr_result.text.strip()
                    ocr_bbox = ocr_result.bbox
                else:
                    ocr_text = ocr_result.strip()
            except Exception:
                ocr_text = ""
            if len(ocr_text) > len(text):
                text = ocr_text
                extraction_method = f"ocr:{backend.name}"
            else:
                extraction_method = "text"
        else:
            extraction_method = "text"
        if not text:
            continue
        if layout and extraction_method == "text":
            positioned = _extract_pdf_blocks(page)
            if positioned:
                result.extend(_merge_pdf_blocks(positioned, display_path, page_number, size))
                continue
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        current: list[str] = []
        length = 0
        for paragraph in paragraphs or [text]:
            if current and length + len(paragraph) > size:
                result.append(
                    ParsedChunk(
                        content="\n\n".join(current),
                        locator=f"{display_path}#page={page_number}",
                        path=display_path,
                        page=page_number,
                        bbox=ocr_bbox,
                        block_type="paragraph",
                        reading_order=len(result),
                        extraction_method=extraction_method,
                    )
                )
                current, length = [], 0
            current.append(paragraph)
            length += len(paragraph)
        if current:
            result.append(
                ParsedChunk(
                    content="\n\n".join(current),
                    locator=f"{display_path}#page={page_number}",
                    path=display_path,
                    page=page_number,
                    bbox=ocr_bbox,
                    block_type="paragraph",
                    reading_order=len(result),
                    extraction_method=extraction_method,
                )
            )
    return result


def chunk_code(
    text: str,
    display_path: str,
    *,
    suffix: str,
    size: int,
    overlap: int,
    prefix: str,
) -> list[ParsedChunk]:
    """Split source around symbols, falling back to bounded line windows."""

    lines = text.splitlines()
    symbols: list[tuple[int, int, str, str]] = []
    if suffix in {".py", ".pyi"}:
        try:
            tree = parse(text)
            def collect_python_symbols(nodes: list, prefix: str = "") -> None:
                for node in nodes:
                    if not isinstance(node, (ClassDef, FunctionDef, AsyncFunctionDef)):
                        continue
                    qualified_name = f"{prefix}.{node.name}" if prefix else node.name
                    symbols.append(
                        (
                            node.lineno,
                            getattr(node, "end_lineno", node.lineno),
                            qualified_name,
                            "class" if isinstance(node, ClassDef) else "function",
                        )
                    )
                    if isinstance(node, ClassDef):
                        collect_python_symbols(node.body, qualified_name)

            collect_python_symbols(tree.body)
        except SyntaxError:
            pass
    if not symbols:
        pattern = re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?(?:class|interface|trait|struct|enum|"
            r"function|fn|func)\s+([A-Za-z_$][\w$]*)|"
            r"^\s*(?:public|private|protected|static|final|async|const|virtual|override|\s)+"
            r"[\w<>,.?:\[\]]+\s+([A-Za-z_$][\w$]*)\s*\("
        )
        starts: list[tuple[int, str]] = []
        for index, line in enumerate(lines, start=1):
            match = pattern.match(line)
            if match:
                starts.append((index, next(group for group in match.groups() if group)))
        for index, (start, name) in enumerate(starts):
            end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
            symbols.append((start, end, name, "symbol"))
    if not symbols:
        return _window_lines(
            text, display_path, size=size, overlap=overlap, prefix=prefix, block_type="code"
        )

    result: list[ParsedChunk] = []
    for start, end, name, kind in symbols:
        symbol_text = "\n".join(lines[start - 1 : end])
        if len(symbol_text) > size * 2:
            windows = _window_lines(
                symbol_text,
                display_path,
                size=size,
                overlap=overlap,
                prefix=prefix,
                heading=name,
                block_type=kind,
            )
            for window in windows:
                window.start_line = (window.start_line or 1) + start - 1
                window.end_line = (window.end_line or 1) + start - 1
                window.locator = f"{prefix}{display_path}#L{window.start_line}-L{window.end_line}"
                window.section = name
                window.reading_order = len(result)
            result.extend(windows)
        else:
            result.append(
                ParsedChunk(
                    content=symbol_text,
                    locator=f"{prefix}{display_path}#L{start}-L{end}",
                    path=display_path,
                    start_line=start,
                    end_line=end,
                    heading=name,
                    section=name,
                    block_type=kind,
                    reading_order=len(result),
                )
            )
    return result


def chunk_path(
    path: Path,
    display_path: str | None = None,
    *,
    size: int = 1200,
    overlap: int = 120,
    locator_prefix: str = "",
    pdf_layout: bool = True,
    pdf_ocr_enabled: bool = True,
    pdf_ocr_min_chars: int = 40,
    pdf_ocr_dpi: int = 200,
    pdf_ocr_language: str = "eng",
    ocr_backend: OCRBackend | None = None,
) -> list[ParsedChunk]:
    display = display_path or path.name
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return chunk_pdf(
            path,
            display,
            size=size,
            layout=pdf_layout,
            ocr_enabled=pdf_ocr_enabled,
            ocr_min_chars=pdf_ocr_min_chars,
            ocr_dpi=pdf_ocr_dpi,
            ocr_language=pdf_ocr_language,
            ocr_backend=ocr_backend,
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".md", ".markdown", ".rst"}:
        return chunk_markdown(text, display, size=size, overlap=overlap, prefix=locator_prefix)
    if suffix in CODE_EXTENSIONS:
        return chunk_code(
            text,
            display,
            suffix=suffix,
            size=size,
            overlap=overlap,
            prefix=locator_prefix,
        )
    return _window_lines(text, display, size=size, overlap=overlap, prefix=locator_prefix)
