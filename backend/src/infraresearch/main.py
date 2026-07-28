from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import router, running_threads
from .config import get_settings
from .database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().ensure_directories()
    init_db()
    yield
    for thread in list(running_threads):
        thread.join(timeout=2)


app = FastAPI(
    title="InfraResearch Agent API",
    version=__version__,
    description="Explainable Agentic RAG for infrastructure research.",
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
