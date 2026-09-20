from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="INFRARESEARCH_",
        extra="ignore",
    )

    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/infraresearch.db"
    vector_backend: str = "qdrant"
    embedding_backend: str = "fastembed"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimensions: int = 384
    embedding_cache_dir: Path = Path("~/.cache/fastembed")
    embedding_local_files_only: bool = False
    reranker_backend: str = "identity"
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_cache_dir: Path = Path("~/.cache/fastembed")
    reranker_local_files_only: bool = False
    candidate_k: int = 20
    evidence_k: int = 6
    chunk_size: int = 1200
    chunk_overlap: int = 120
    llm_base_url: str = "http://127.0.0.1:8001/v1"
    llm_api_key: str = "local"
    llm_model: str = "Qwen/Qwen3-0.6B"
    llm_timeout_seconds: float = 60
    max_upload_mb: int = 25
    max_repo_files: int = 2000
    max_agent_rewrites: int = 2
    token_budget: int = 6000
    citation_repair_enabled: bool = True
    worker_poll_seconds: float = 0.25
    worker_heartbeat_seconds: float = 2
    worker_stale_seconds: float = 30
    github_token: str | None = None
    cors_origins: str = "http://localhost:5173"

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def repos_path(self) -> Path:
        return self.data_dir / "repos"

    @property
    def resolved_embedding_cache_dir(self) -> Path:
        return self.embedding_cache_dir.expanduser()

    @property
    def resolved_reranker_cache_dir(self) -> Path:
        return self.reranker_cache_dir.expanduser()

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repos_path.mkdir(parents=True, exist_ok=True)
        self.resolved_embedding_cache_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_reranker_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
