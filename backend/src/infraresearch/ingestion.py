from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .chunking import CODE_EXTENSIONS, chunk_path, is_indexable
from .config import Settings
from .errors import TaskCancelled
from .models import Chunk, Ingestion, Source, Status
from .retrieval import VectorIndex


def source_to_metadata(
    settings: Settings, embedding_backend: str | None = None
) -> dict[str, str | int]:
    return {
        "embedding_backend": embedding_backend or settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
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
            parsed = chunk_path(
                path,
                source.name,
                size=self.settings.chunk_size,
                overlap=self.settings.chunk_overlap,
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
            source.metadata_json = json.dumps(
                source_to_metadata(self.settings, self.vector_index.embedding_backend)
            )
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
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            self._run_command(
                ["git", "clone", "--depth", "1", "--filter=blob:none", source.uri, str(repo_dir)],
                timeout=180,
                cancel_check=cancel_check,
            )
            if revision:
                self._run_command(
                    ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", revision],
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
                parsed_chunks.extend(
                    chunk_path(
                        path,
                        relative,
                        size=self.settings.chunk_size,
                        overlap=self.settings.chunk_overlap,
                        locator_prefix=prefix,
                    )
                )
            chunks = self._replace_chunks(
                session,
                source,
                parsed_chunks,
                cancel_check=cancel_check,
            )
            if include_issues:
                self._checkpoint(cancel_check)
                issue_count = self._ingest_issues(session, owner, repo, sha)
            else:
                issue_count = 0
            ingestion.files_seen = files_seen
            ingestion.chunks_indexed = len(chunks) + issue_count
            self._checkpoint(cancel_check)
            self.vector_index.replace_source(source.id, chunks)
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

    def _replace_chunks(
        self,
        session: Session,
        source: Source,
        parsed: list,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[Chunk]:
        session.execute(delete(Chunk).where(Chunk.source_id == source.id))
        chunks: list[Chunk] = []
        for item in parsed:
            self._checkpoint(cancel_check)
            suffix = Path(item.path or "").suffix.lower()
            category = "code" if suffix in CODE_EXTENSIONS else "docs"
            chunk = Chunk(
                source_id=source.id,
                content=item.content,
                locator=item.locator,
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                page=item.page,
                heading=item.heading,
                metadata_json=json.dumps({"category": category}),
                content_hash=item.content_hash,
            )
            session.add(chunk)
            chunks.append(chunk)
        session.flush()
        return chunks

    def _ingest_issues(self, session: Session, owner: str, repo: str, sha: str) -> int:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        response = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            headers=headers,
            params={"state": "all", "per_page": 100},
            timeout=30,
        )
        if response.status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining", "unknown")
            raise RuntimeError(f"GitHub API rate limited (remaining={remaining}); add GITHUB_TOKEN")
        response.raise_for_status()
        issues = [item for item in response.json() if "pull_request" not in item]
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
        session.execute(delete(Chunk).where(Chunk.source_id == issue_source.id))
        chunks = []
        for issue in issues:
            number = int(issue["number"])
            content = f"# {issue.get('title', '')}\n\n{issue.get('body') or ''}".strip()
            chunk = Chunk(
                source_id=issue_source.id,
                content=content,
                locator=f"{owner}/{repo}#issue-{number} {issue['html_url']}",
                path=None,
                metadata_json=json.dumps(
                    {"category": "issue", "number": number, "url": issue["html_url"]}
                ),
                content_hash=__import__("hashlib").sha256(content.encode()).hexdigest(),
            )
            session.add(chunk)
            chunks.append(chunk)
        session.flush()
        self.vector_index.replace_source(issue_source.id, chunks)
        return len(chunks)

    @staticmethod
    def _checkpoint(cancel_check: Callable[[], None] | None) -> None:
        if cancel_check:
            cancel_check()

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
