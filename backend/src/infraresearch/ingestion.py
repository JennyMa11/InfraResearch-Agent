from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .chunking import CODE_EXTENSIONS, chunk_markdown, chunk_path, is_indexable
from .config import Settings
from .errors import TaskCancelled
from .models import Chunk, Ingestion, Source, Status
from .retrieval import VectorIndex
from .web import fetch_web_page


def source_to_metadata(
    settings: Settings, embedding_backend: str | None = None
) -> dict[str, str | int]:
    return {
        "embedding_backend": embedding_backend or settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "pdf_layout_enabled": int(settings.pdf_layout_enabled),
        "pdf_ocr_enabled": int(settings.pdf_ocr_enabled),
        "retrieval_mode": settings.retrieval_mode,
    }


class IngestionService:
    def __init__(self, settings: Settings, vector_index: VectorIndex):
        self.settings = settings
        self.vector_index = vector_index

    def ingest_file(
        self,
        session: Session,
        source_id: str,
        ingestion_id: str,
        path: Path,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        source = session.get(Source, source_id)
        ingestion = session.get(Ingestion, ingestion_id)
        if not source or not ingestion:
            return
        ingestion.status = source.status = Status.RUNNING
        ingestion.started_at = datetime.now(UTC)
        session.commit()
        try:
            self._checkpoint(cancel_check)
            file_hash = self._file_hash(path)
            previous_metadata = json.loads(source.metadata_json or "{}")
            existing_chunks = int(
                session.scalar(
                    select(func.count(Chunk.id)).where(Chunk.source_id == source.id)
                )
                or 0
            )
            if previous_metadata.get("file_hash") == file_hash and existing_chunks:
                ingestion.files_seen = 1
                ingestion.chunks_indexed = existing_chunks
                ingestion.status = source.status = Status.COMPLETED
                ingestion.completed_at = datetime.now(UTC)
                previous_metadata["incremental_status"] = "unchanged"
                source.metadata_json = json.dumps(previous_metadata)
                session.commit()
                return
            parsed = chunk_path(
                path,
                source.name,
                size=self.settings.chunk_size,
                overlap=self.settings.chunk_overlap,
                pdf_layout=self.settings.pdf_layout_enabled,
                pdf_ocr_enabled=self.settings.pdf_ocr_enabled,
                pdf_ocr_min_chars=self.settings.pdf_ocr_min_chars,
                pdf_ocr_dpi=self.settings.pdf_ocr_dpi,
                pdf_ocr_language=self.settings.pdf_ocr_language,
            )
            self._checkpoint(cancel_check)
            chunks = self._replace_chunks(
                session,
                source,
                parsed,
                cancel_check=cancel_check,
            )
            ingestion.files_seen = 1
            ingestion.chunks_indexed = len(chunks)
            self._checkpoint(cancel_check)
            self.vector_index.replace_source(source.id, chunks)
            self._checkpoint(cancel_check)
            ingestion.status = source.status = Status.COMPLETED
            ingestion.completed_at = datetime.now(UTC)
            metadata = source_to_metadata(self.settings, self.vector_index.embedding_backend)
            metadata.update({"file_hash": file_hash, "incremental_status": "indexed"})
            source.metadata_json = json.dumps(metadata)
            session.commit()
        except TaskCancelled:
            session.rollback()
            self._mark_cancelled(session, source_id, ingestion_id)
            raise
        except Exception as exc:
            session.rollback()
            source = session.get(Source, source_id)
            ingestion = session.get(Ingestion, ingestion_id)
            if source and ingestion:
                source.status = ingestion.status = Status.FAILED
                source.error = ingestion.error = str(exc)[:2000]
                ingestion.completed_at = datetime.now(UTC)
                session.commit()

    def ingest_github(
        self,
        session: Session,
        source_id: str,
        ingestion_id: str,
        *,
        revision: str | None,
        include_issues: bool,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        source = session.get(Source, source_id)
        ingestion = session.get(Ingestion, ingestion_id)
        if not source or not ingestion:
            return
        ingestion.status = source.status = Status.RUNNING
        ingestion.started_at = datetime.now(UTC)
        session.commit()
        try:
            self._checkpoint(cancel_check)
            owner, repo = parse_github_url(source.uri)
            repo_dir = self.settings.repos_path / source.id
            previous_sha = source.revision
            existing_checkout = (repo_dir / ".git").is_dir()
            if existing_checkout:
                fetch_command = [
                    "git",
                    "-C",
                    str(repo_dir),
                    "fetch",
                    "--depth",
                    "2",
                    "origin",
                ]
                if revision:
                    fetch_command.append(revision)
                self._run_command(
                    fetch_command,
                    timeout=180,
                    cancel_check=cancel_check,
                )
                self._run_command(
                    ["git", "-C", str(repo_dir), "checkout", "--detach", "FETCH_HEAD"],
                    timeout=60,
                    cancel_check=cancel_check,
                )
            else:
                if repo_dir.exists():
                    shutil.rmtree(repo_dir)
                self._run_command(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--filter=blob:none",
                        source.uri,
                        str(repo_dir),
                    ],
                    timeout=180,
                    cancel_check=cancel_check,
                )
                if revision:
                    self._run_command(
                        [
                            "git",
                            "-C",
                            str(repo_dir),
                            "fetch",
                            "--depth",
                            "1",
                            "origin",
                            revision,
                        ],
                        timeout=180,
                        cancel_check=cancel_check,
                    )
                    self._run_command(
                        ["git", "-C", str(repo_dir), "checkout", "--detach", "FETCH_HEAD"],
                        timeout=60,
                        cancel_check=cancel_check,
                    )
            sha = self._run_command(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                timeout=30,
                cancel_check=cancel_check,
            ).stdout.strip()
            source.revision = sha
            parsed_chunks = []
            files_seen = 0
            prefix = f"{owner}/{repo}@{sha}/"
            previous_metadata = json.loads(source.metadata_json or "{}")
            chunk_config_changed = any(
                previous_metadata.get(key) != value
                for key, value in {
                    "chunk_size": self.settings.chunk_size,
                    "chunk_overlap": self.settings.chunk_overlap,
                    "pdf_layout_enabled": int(self.settings.pdf_layout_enabled),
                    "pdf_ocr_enabled": int(self.settings.pdf_ocr_enabled),
                }.items()
            )
            changed_paths, deleted_paths = self._changed_repo_paths(
                repo_dir,
                previous_sha,
                sha,
                cancel_check=cancel_check,
            )
            full_reindex = (
                not existing_checkout
                or previous_sha is None
                or chunk_config_changed
                or changed_paths is None
            )
            changed_paths = changed_paths or set()
            for path in sorted(repo_dir.rglob("*")):
                self._checkpoint(cancel_check)
                if not path.is_file() or not is_indexable(path.relative_to(repo_dir)):
                    continue
                if path.stat().st_size > self.settings.max_upload_mb * 1024 * 1024:
                    continue
                files_seen += 1
                if files_seen > self.settings.max_repo_files:
                    raise ValueError(
                        f"repository exceeds {self.settings.max_repo_files} indexable files"
                    )
                relative = path.relative_to(repo_dir).as_posix()
                if not full_reindex and relative not in changed_paths:
                    continue
                parsed_chunks.extend(
                    chunk_path(
                        path,
                        relative,
                        size=self.settings.chunk_size,
                        overlap=self.settings.chunk_overlap,
                        locator_prefix=prefix,
                        pdf_layout=self.settings.pdf_layout_enabled,
                        pdf_ocr_enabled=self.settings.pdf_ocr_enabled,
                        pdf_ocr_min_chars=self.settings.pdf_ocr_min_chars,
                        pdf_ocr_dpi=self.settings.pdf_ocr_dpi,
                        pdf_ocr_language=self.settings.pdf_ocr_language,
                    )
                )
            if full_reindex:
                chunks = self._replace_chunks(
                    session,
                    source,
                    parsed_chunks,
                    cancel_check=cancel_check,
                )
                self.vector_index.replace_source(source.id, chunks)
                changed_file_count = files_seen
                deleted_file_count = 0
            else:
                replaced_paths = changed_paths | deleted_paths
                stale_chunks = session.scalars(
                    select(Chunk).where(
                        Chunk.source_id == source.id,
                        Chunk.path.in_(replaced_paths),
                    )
                ).all() if replaced_paths else []
                stale_ids = [chunk.id for chunk in stale_chunks]
                if stale_ids:
                    session.execute(delete(Chunk).where(Chunk.id.in_(stale_ids)))
                chunks = self._append_chunks(
                    session,
                    source,
                    parsed_chunks,
                    cancel_check=cancel_check,
                )
                self.vector_index.delete_chunks(stale_ids)
                self.vector_index.upsert(chunks)
                changed_file_count = len(changed_paths)
                deleted_file_count = len(deleted_paths)
            if include_issues:
                self._checkpoint(cancel_check)
                issue_count = self._ingest_issues(session, owner, repo, sha)
            else:
                issue_count = 0
            ingestion.files_seen = files_seen
            ingestion.chunks_indexed = int(
                session.scalar(
                    select(func.count(Chunk.id)).where(Chunk.source_id == source.id)
                )
                or 0
            ) + issue_count
            self._checkpoint(cancel_check)
            ingestion.status = source.status = Status.COMPLETED
            ingestion.completed_at = datetime.now(UTC)
            metadata = source_to_metadata(self.settings, self.vector_index.embedding_backend)
            metadata.update(
                {
                    "owner": owner,
                    "repo": repo,
                    "issues_indexed": issue_count,
                    "include_issues": include_issues,
                    "requested_revision": revision,
                    "incremental_status": (
                        "full"
                        if full_reindex
                        else "unchanged"
                        if not changed_paths and not deleted_paths
                        else "updated"
                    ),
                    "changed_files": changed_file_count,
                    "deleted_files": deleted_file_count,
                }
            )
            source.metadata_json = json.dumps(metadata)
            session.commit()
        except TaskCancelled:
            session.rollback()
            self._mark_cancelled(session, source_id, ingestion_id)
            raise
        except Exception as exc:
            session.rollback()
            source = session.get(Source, source_id)
            ingestion = session.get(Ingestion, ingestion_id)
            if source and ingestion:
                source.status = ingestion.status = Status.FAILED
                source.error = ingestion.error = str(exc)[:2000]
                ingestion.completed_at = datetime.now(UTC)
                session.commit()

    def ingest_url(
        self,
        session: Session,
        source_id: str,
        ingestion_id: str,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        source = session.get(Source, source_id)
        ingestion = session.get(Ingestion, ingestion_id)
        if not source or not ingestion:
            return
        ingestion.status = source.status = Status.RUNNING
        ingestion.started_at = datetime.now(UTC)
        session.commit()
        try:
            self._checkpoint(cancel_check)
            page = fetch_web_page(
                source.uri,
                max_bytes=self.settings.max_web_mb * 1024 * 1024,
            )
            self._checkpoint(cancel_check)
            content_hash = hashlib.sha256(page.text.encode()).hexdigest()
            previous_metadata = json.loads(source.metadata_json or "{}")
            existing_count = int(
                session.scalar(
                    select(func.count(Chunk.id)).where(Chunk.source_id == source.id)
                )
                or 0
            )
            if previous_metadata.get("content_hash") == content_hash and existing_count:
                chunks = session.scalars(
                    select(Chunk).where(Chunk.source_id == source.id)
                ).all()
                incremental_status = "unchanged"
            else:
                parsed = chunk_markdown(
                    page.text,
                    page.url,
                    size=self.settings.chunk_size,
                    overlap=self.settings.chunk_overlap,
                )
                chunks = self._replace_chunks(
                    session,
                    source,
                    parsed,
                    cancel_check=cancel_check,
                )
                self.vector_index.replace_source(source.id, chunks)
                incremental_status = "indexed"
            if page.title:
                source.name = page.title[:512]
            metadata = source_to_metadata(self.settings, self.vector_index.embedding_backend)
            metadata.update(
                {
                    "content_hash": content_hash,
                    "content_type": page.content_type,
                    "final_url": page.url,
                    "etag": page.etag,
                    "last_modified": page.last_modified,
                    "incremental_status": incremental_status,
                }
            )
            source.metadata_json = json.dumps(metadata)
            ingestion.files_seen = 1
            ingestion.chunks_indexed = len(chunks)
            ingestion.status = source.status = Status.COMPLETED
            ingestion.completed_at = datetime.now(UTC)
            session.commit()
        except TaskCancelled:
            session.rollback()
            self._mark_cancelled(session, source_id, ingestion_id)
            raise
        except Exception as exc:
            session.rollback()
            source = session.get(Source, source_id)
            ingestion = session.get(Ingestion, ingestion_id)
            if source and ingestion:
                source.status = ingestion.status = Status.FAILED
                source.error = ingestion.error = str(exc)[:2000]
                ingestion.completed_at = datetime.now(UTC)
                session.commit()

    def _replace_chunks(
        self,
        session: Session,
        source: Source,
        parsed: list,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[Chunk]:
        session.execute(delete(Chunk).where(Chunk.source_id == source.id))
        return self._append_chunks(
            session,
            source,
            parsed,
            cancel_check=cancel_check,
        )

    def _append_chunks(
        self,
        session: Session,
        source: Source,
        parsed: list,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        for item in parsed:
            self._checkpoint(cancel_check)
            suffix = Path(item.path or "").suffix.lower()
            category = (
                "web"
                if source.kind in {"web", "web_search"}
                else "code"
                if suffix in CODE_EXTENSIONS
                else "docs"
            )
            chunk = Chunk(
                source_id=source.id,
                content=item.content,
                locator=item.locator,
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                page=item.page,
                heading=item.heading,
                metadata_json=json.dumps(
                    {
                        "category": category,
                        "section": item.section,
                        "bbox": list(item.bbox) if item.bbox else None,
                        "block_type": item.block_type,
                        "reading_order": item.reading_order,
                        "extraction_method": item.extraction_method,
                    }
                ),
                content_hash=item.content_hash,
            )
            session.add(chunk)
            chunks.append(chunk)
        session.flush()
        return chunks

    def _changed_repo_paths(
        self,
        repo_dir: Path,
        previous_sha: str | None,
        sha: str,
        *,
        cancel_check: Callable[[], None] | None,
    ) -> tuple[set[str] | None, set[str]]:
        if not previous_sha or previous_sha == sha:
            return set(), set()
        try:
            output = self._run_command(
                [
                    "git",
                    "-C",
                    str(repo_dir),
                    "diff",
                    "--name-status",
                    previous_sha,
                    sha,
                    "--",
                ],
                timeout=60,
                cancel_check=cancel_check,
            ).stdout
        except subprocess.CalledProcessError:
            # A shallow fetch may not retain the old commit. Re-index everything
            # rather than incorrectly treating the checkout as unchanged.
            return None, set()
        changed: set[str] = set()
        deleted: set[str] = set()
        for line in output.splitlines():
            fields = line.split("\t")
            status = fields[0]
            if status.startswith("R") and len(fields) >= 3:
                deleted.add(fields[1])
                changed.add(fields[2])
            elif len(fields) >= 2 and status == "D":
                deleted.add(fields[1])
            elif len(fields) >= 2:
                changed.add(fields[1])
        return changed, deleted

    def _ingest_issues(self, session: Session, owner: str, repo: str, sha: str) -> int:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        issues: list[dict] = []
        page = 1
        while len(issues) < self.settings.max_github_issues:
            response = httpx.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                headers=headers,
                params={"state": "all", "per_page": 100, "page": page},
                timeout=30,
            )
            if response.status_code == 403:
                remaining = response.headers.get("x-ratelimit-remaining", "unknown")
                raise RuntimeError(
                    f"GitHub API rate limited (remaining={remaining}); add GITHUB_TOKEN"
                )
            response.raise_for_status()
            batch = list(response.json())
            issues.extend(item for item in batch if "pull_request" not in item)
            if len(batch) < 100:
                break
            page += 1
        issues = issues[: self.settings.max_github_issues]
        issue_uri = f"https://github.com/{owner}/{repo}/issues"
        issue_source = session.scalar(
            select(Source).where(Source.kind == "issue", Source.uri == issue_uri)
        )
        if issue_source is None:
            issue_source = Source(
                kind="issue",
                name=f"{owner}/{repo} issues",
                uri=issue_uri,
            )
            session.add(issue_source)
        issue_source.status = Status.COMPLETED
        issue_source.revision = sha
        issue_source.error = None
        issue_source.metadata_json = json.dumps(
            {
                **source_to_metadata(self.settings, self.vector_index.embedding_backend),
                "parent_repository": f"{owner}/{repo}",
            }
        )
        session.flush()
        existing_chunks = session.scalars(
            select(Chunk).where(Chunk.source_id == issue_source.id)
        ).all()
        existing_by_number = {
            json.loads(chunk.metadata_json).get("number"): chunk for chunk in existing_chunks
        }
        changed_chunks: list[Chunk] = []
        seen_numbers: set[int] = set()
        latest_updated_at: str | None = None
        for issue in issues:
            number = int(issue["number"])
            seen_numbers.add(number)
            content = f"# {issue.get('title', '')}\n\n{issue.get('body') or ''}".strip()
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            updated_at = issue.get("updated_at")
            if updated_at and (latest_updated_at is None or updated_at > latest_updated_at):
                latest_updated_at = updated_at
            metadata = {
                "category": "issue",
                "number": number,
                "url": issue["html_url"],
                "updated_at": updated_at,
            }
            chunk = existing_by_number.get(number)
            is_new = chunk is None
            if is_new:
                chunk = Chunk(source_id=issue_source.id, content_hash=content_hash)
                session.add(chunk)
            if (
                is_new
                or chunk.content_hash != content_hash
                or json.loads(chunk.metadata_json) != metadata
            ):
                chunk.content = content
                chunk.locator = f"{owner}/{repo}#issue-{number} {issue['html_url']}"
                chunk.path = None
                chunk.metadata_json = json.dumps(metadata)
                chunk.content_hash = content_hash
                changed_chunks.append(chunk)
        stale = [
            chunk for number, chunk in existing_by_number.items() if number not in seen_numbers
        ]
        stale_ids = [chunk.id for chunk in stale]
        if stale_ids:
            session.execute(delete(Chunk).where(Chunk.id.in_(stale_ids)))
        session.flush()
        self.vector_index.delete_chunks(stale_ids)
        self.vector_index.upsert(changed_chunks)
        issue_metadata = source_to_metadata(
            self.settings, self.vector_index.embedding_backend
        )
        issue_metadata.update(
            {
                "parent_repository": f"{owner}/{repo}",
                "last_issue_updated_at": latest_updated_at,
                "issues_changed": len(changed_chunks),
                "issues_deleted": len(stale_ids),
            }
        )
        issue_source.metadata_json = json.dumps(issue_metadata)
        return len(issues)

    @staticmethod
    def _checkpoint(cancel_check: Callable[[], None] | None) -> None:
        if cancel_check:
            cancel_check()

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _mark_cancelled(session: Session, source_id: str, ingestion_id: str) -> None:
        source = session.get(Source, source_id)
        ingestion = session.get(Ingestion, ingestion_id)
        if source and ingestion:
            source.status = ingestion.status = Status.CANCELLED
            source.error = ingestion.error = None
            ingestion.completed_at = datetime.now(UTC)
            session.commit()

    def _run_command(
        self,
        command: list[str],
        *,
        timeout: float,
        cancel_check: Callable[[], None] | None,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started = time.monotonic()
        try:
            while True:
                self._checkpoint(cancel_check)
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired as exc:
                    if time.monotonic() - started >= timeout:
                        raise subprocess.TimeoutExpired(command, timeout) from exc
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            if process.returncode:
                raise subprocess.CalledProcessError(
                    process.returncode,
                    command,
                    output=stdout,
                    stderr=stderr,
                )
            return result
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise


def parse_github_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("only public https://github.com repositories are allowed")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("GitHub URL must be https://github.com/owner/repository")
    owner, repo = parts
    return owner, repo.removesuffix(".git")
