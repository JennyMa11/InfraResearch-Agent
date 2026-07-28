from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .chunking import CODE_EXTENSIONS, chunk_path, is_indexable
from .config import Settings
from .models import Chunk, Ingestion, Source, Status
from .retrieval import VectorIndex


def source_to_metadata(settings: Settings) -> dict[str, str | int]:
    return {
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }


class IngestionService:
    def __init__(self, settings: Settings, vector_index: VectorIndex):
        self.settings = settings
        self.vector_index = vector_index

    def ingest_file(self, session: Session, source_id: str, ingestion_id: str, path: Path) -> None:
        source = session.get(Source, source_id)
        ingestion = session.get(Ingestion, ingestion_id)
        if not source or not ingestion:
            return
        ingestion.status = source.status = Status.RUNNING
        ingestion.started_at = datetime.now(UTC)
        session.commit()
        try:
            parsed = chunk_path(
                path,
                source.name,
                size=self.settings.chunk_size,
                overlap=self.settings.chunk_overlap,
            )
            chunks = self._replace_chunks(session, source, parsed)
            ingestion.files_seen = 1
            ingestion.chunks_indexed = len(chunks)
            ingestion.status = source.status = Status.COMPLETED
            ingestion.completed_at = datetime.now(UTC)
            source.metadata_json = json.dumps(source_to_metadata(self.settings))
            session.commit()
            self.vector_index.upsert(chunks)
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
    ) -> None:
        source = session.get(Source, source_id)
        ingestion = session.get(Ingestion, ingestion_id)
        if not source or not ingestion:
            return
        ingestion.status = source.status = Status.RUNNING
        ingestion.started_at = datetime.now(UTC)
        session.commit()
        try:
            owner, repo = parse_github_url(source.uri)
            repo_dir = self.settings.repos_path / source.id
            subprocess.run(
                ["git", "clone", "--depth", "1", "--filter=blob:none", source.uri, str(repo_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if revision:
                subprocess.run(
                    ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", revision],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                subprocess.run(
                    ["git", "-C", str(repo_dir), "checkout", "--detach", "FETCH_HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            sha = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source.revision = sha
            parsed_chunks = []
            files_seen = 0
            prefix = f"{owner}/{repo}@{sha}/"
            for path in sorted(repo_dir.rglob("*")):
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
            chunks = self._replace_chunks(session, source, parsed_chunks)
            if include_issues:
                issue_count = self._ingest_issues(session, owner, repo, sha)
            else:
                issue_count = 0
            ingestion.files_seen = files_seen
            ingestion.chunks_indexed = len(chunks) + issue_count
            ingestion.status = source.status = Status.COMPLETED
            ingestion.completed_at = datetime.now(UTC)
            metadata = source_to_metadata(self.settings)
            metadata.update({"owner": owner, "repo": repo, "issues_indexed": issue_count})
            source.metadata_json = json.dumps(metadata)
            session.commit()
            self.vector_index.upsert(chunks)
        except Exception as exc:
            session.rollback()
            source = session.get(Source, source_id)
            ingestion = session.get(Ingestion, ingestion_id)
            if source and ingestion:
                source.status = ingestion.status = Status.FAILED
                source.error = ingestion.error = str(exc)[:2000]
                ingestion.completed_at = datetime.now(UTC)
                session.commit()

    def _replace_chunks(self, session: Session, source: Source, parsed: list) -> list[Chunk]:
        session.execute(delete(Chunk).where(Chunk.source_id == source.id))
        chunks: list[Chunk] = []
        for item in parsed:
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
        if not issues:
            return 0
        issue_source = Source(
            kind="issue",
            name=f"{owner}/{repo} issues",
            uri=f"https://github.com/{owner}/{repo}/issues",
            status=Status.COMPLETED,
            revision=sha,
            metadata_json=json.dumps({"parent_repository": f"{owner}/{repo}"}),
        )
        session.add(issue_source)
        session.flush()
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
        self.vector_index.upsert(chunks)
        return len(chunks)


def parse_github_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("only public https://github.com repositories are allowed")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("GitHub URL must be https://github.com/owner/repository")
    owner, repo = parts
    return owner, repo.removesuffix(".git")
