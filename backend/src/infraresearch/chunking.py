from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

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
    for idx in range(len(boundaries) - 1):
        start, end = boundaries[idx], boundaries[idx + 1]
        heading = re.sub(r"^#{1,6}\s+", "", lines[start]).strip()
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
                )
            )
        else:
            windows = _window_lines(
                section, display_path, size=size, overlap=overlap, prefix=prefix
            )
            for window in windows:
                window.start_line = (window.start_line or 1) + start
                window.end_line = (window.end_line or 1) + start
                window.locator = f"{prefix}{display_path}#L{window.start_line}-L{window.end_line}"
                window.heading = heading
            result.extend(windows)
    return result


def chunk_pdf(path: Path, display_path: str, *, size: int = 1200) -> list[ParsedChunk]:
    reader = PdfReader(str(path))
    result: list[ParsedChunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
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
) -> list[ParsedChunk]:
    display = display_path or path.name
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return chunk_pdf(path, display, size=size)
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".md", ".markdown", ".rst"}:
        return chunk_markdown(text, display, size=size, overlap=overlap, prefix=locator_prefix)
    return _window_lines(text, display, size=size, overlap=overlap, prefix=locator_prefix)
