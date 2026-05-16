"""FastAPI entry point.

Run locally:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure project loggers once. Override level via APP_LOG_LEVEL env var.
logging.basicConfig(
    level=os.getenv("APP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

from app.api import (
    agent_routes,
    application_routes,
    cv_render_routes,
    cv_routes,
    embeddings_routes,
    generate_routes,
    job_routes,
    match_routes,
    profile_routes,
    search_routes,
    source_routes,
    tailor_routes,
)
from app.db.database import init_db

# Comma-separated origins, e.g. "http://localhost:3000,http://localhost:5173".
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("APP_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="AI Job-CV Matching Agent",
    description="Phase 1 backend: upload CVs, parse JDs, score matches.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup() -> None:
    init_db()


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(cv_routes.router)
app.include_router(job_routes.router)
app.include_router(match_routes.router)
app.include_router(embeddings_routes.router)
app.include_router(search_routes.router)
app.include_router(profile_routes.router)
app.include_router(tailor_routes.router)
app.include_router(agent_routes.router)
app.include_router(generate_routes.router)
app.include_router(cv_render_routes.router)
app.include_router(application_routes.router)
app.include_router(source_routes.router)
