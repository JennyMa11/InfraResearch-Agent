#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from infraresearch.chunking import chunk_pdf
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader


def create_image_only_pdf(path: Path) -> None:
    image = Image.new("RGB", (1800, 600), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_path), 72) if font_path.exists() else ImageFont.load_default()
    draw.text((90, 120), "PREFIX CACHE REUSES KV BLOCKS", fill="black", font=font)
    draw.text((90, 260), "SCANNED PAGE EVIDENCE 2026", fill="black", font=font)
    image.save(path, "PDF", resolution=200)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real Tesseract OCR on an image-only PDF")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/results/ocr-real.json"),
    )
    args = parser.parse_args()

    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise SystemExit("tesseract is not available on PATH")
    version = subprocess.run(
        [tesseract, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[0]

    with tempfile.TemporaryDirectory(prefix="infraresearch-ocr-") as directory:
        pdf = Path(directory) / "scanned-evidence.pdf"
        create_image_only_pdf(pdf)
        text_layer = "".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
        if text_layer.strip():
            raise SystemExit("fixture unexpectedly contains a PDF text layer")
        chunks = chunk_pdf(
            pdf,
            pdf.name,
            ocr_enabled=True,
            ocr_min_chars=40,
            ocr_dpi=200,
            ocr_language="eng",
        )

    if not chunks or "PREFIX CACHE" not in chunks[0].content.upper():
        raise SystemExit("OCR did not recover the expected text")
    if chunks[0].bbox is None:
        raise SystemExit("OCR did not preserve a bounding box")
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tesseract": version,
        "fixture": "generated image-only PDF with no text layer",
        "chunks": [
            {
                "content": chunk.content,
                "locator": chunk.locator,
                "page": chunk.page,
                "bbox": chunk.bbox,
                "extraction_method": chunk.extraction_method,
            }
            for chunk in chunks
        ],
        "status": "passed",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
