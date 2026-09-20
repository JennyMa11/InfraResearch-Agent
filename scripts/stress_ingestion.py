#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from infraresearch.chunking import chunk_path
from infraresearch.config import Settings
from infraresearch.ingestion import IngestionService
from infraresearch.models import Base, Chunk, Ingestion, Source, Status
from infraresearch.retrieval import VectorIndex
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def write_text_pdf(path: Path, pages: int) -> None:
    """Write a dependency-free PDF fixture with one searchable text block per page."""

    objects: list[bytes] = []
    font_object = 3 + pages * 2
    page_objects = [3 + index * 2 for index in range(pages)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{number} 0 R" for number in page_objects)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for index, page_object in enumerate(page_objects, start=1):
        content_object = page_object + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_object} 0 R >> >> "
                f"/Contents {content_object} 0 R >>"
            ).encode()
        )
        stream = (
            f"BT /F1 12 Tf 72 720 Td "
            f"(Page {index}: lease recovery prefix cache evidence section {index}.) Tj ET"
        ).encode()
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic long-document ingestion stress")
    parser.add_argument("--pdf-pages", type=int, default=120)
    parser.add_argument("--repo-files", type=int, default=1000)
    parser.add_argument("--issues", type=int, default=500)
    parser.add_argument("--output", type=Path, default=Path("evals/results/stress-ingestion.json"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="infraresearch-stress-") as directory:
        root = Path(directory)
        settings = Settings(data_dir=root / "data", vector_backend="sqlite", pdf_ocr_enabled=False)
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        index = VectorIndex(settings)
        service = IngestionService(settings, index)
        with Session(engine) as session:
            pdf = root / "long-paper.pdf"
            write_text_pdf(pdf, args.pdf_pages)
            pdf_source = Source(kind="file", name=pdf.name, uri=str(pdf))
            session.add(pdf_source)
            session.flush()
            pdf_ingestion = Ingestion(source_id=pdf_source.id)
            session.add(pdf_ingestion)
            session.commit()
            started = time.perf_counter()
            service.ingest_file(session, pdf_source.id, pdf_ingestion.id, pdf)
            pdf_seconds = time.perf_counter() - started

            repo_source = Source(
                kind="github",
                name="synthetic/large-repo",
                uri="https://github.com/synthetic/large-repo",
                status=Status.RUNNING,
                revision="fixture",
            )
            session.add(repo_source)
            session.flush()
            repo_root = root / "repo"
            repo_root.mkdir()
            started = time.perf_counter()
            parsed = []
            for number in range(args.repo_files):
                path = repo_root / f"module_{number}.py"
                path.write_text(
                    f"class Worker{number}:\n"
                    f"    def recover(self):\n        return 'lease-{number}'\n"
                )
                parsed.extend(
                    chunk_path(
                        path,
                        path.name,
                        locator_prefix="synthetic/large-repo@fixture/",
                    )
                )
            repo_chunks = service._replace_chunks(session, repo_source, parsed)
            repo_source.status = Status.COMPLETED
            session.commit()
            repo_seconds = time.perf_counter() - started

            issue_source = Source(
                kind="issue",
                name="synthetic issues",
                uri="https://github.com/synthetic/large-repo/issues",
                status=Status.COMPLETED,
            )
            session.add(issue_source)
            session.flush()
            started = time.perf_counter()
            for number in range(1, args.issues + 1):
                content = f"Issue {number}: worker lease recovery and heartbeat failure"
                session.add(
                    Chunk(
                        source_id=issue_source.id,
                        content=content,
                        locator=f"synthetic/large-repo#issue-{number}",
                        metadata_json=json.dumps({"category": "issue", "number": number}),
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    )
                )
            session.commit()
            issue_seconds = time.perf_counter() - started

            started = time.perf_counter()
            hits = index.search(session, "worker lease heartbeat recovery", top_k=10)
            retrieval_ms = (time.perf_counter() - started) * 1000
            report = {
                "generated_at": datetime.now(UTC).isoformat(),
                "pdf": {
                    "pages": args.pdf_pages,
                    "chunks": pdf_ingestion.chunks_indexed,
                    "seconds": pdf_seconds,
                    "status": pdf_ingestion.status,
                },
                "repository": {
                    "files": args.repo_files,
                    "chunks": len(repo_chunks),
                    "seconds": repo_seconds,
                },
                "issues": {"count": args.issues, "seconds": issue_seconds},
                "retrieval": {
                    "corpus_chunks": pdf_ingestion.chunks_indexed
                    + len(repo_chunks)
                    + args.issues,
                    "top_k": len(hits),
                    "latency_ms": retrieval_ms,
                },
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
