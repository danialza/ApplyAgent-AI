"""Smoke test that the FastAPI app boots and exposes the expected routes.

Catches obvious regressions like a router not being registered, a circular
import sneaking in, or a typo'd endpoint path. Network-free.

    python -m tests.test_app_startup
"""
from __future__ import annotations

import os

# Use an in-memory DB so the test never touches disk state.
os.environ["APP_DB_URL"] = "sqlite:///:memory:"

from app.main import app  # noqa: E402  (env must be set before import)


EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("GET",    "/api/health"),
    ("POST",   "/api/cvs/upload"),
    ("GET",    "/api/cvs"),
    ("GET",    "/api/cvs/{cv_id}"),
    ("DELETE", "/api/cvs/{cv_id}"),
    ("POST",   "/api/jobs/parse"),
    ("POST",   "/api/jobs/from-url"),
    ("POST",   "/api/jobs/import-csv"),
    ("POST",   "/api/match"),
    ("POST",   "/api/match/single"),
    ("POST",   "/api/match/from-url"),
    ("POST",   "/api/match/batch-csv"),
    ("POST",   "/api/embeddings/rebuild"),
    ("POST",   "/api/search/semantic"),
    ("POST",   "/api/profile/build"),
    ("GET",    "/api/profile"),
    ("DELETE", "/api/profile"),
    ("GET",    "/api/profile/queries"),
    ("POST",   "/api/jobs/discover"),
    ("POST",   "/api/jobs/rank"),
    ("POST",   "/api/jobs/from-file"),
    ("POST",   "/api/match/from-file"),
    ("POST",   "/api/tailor"),
    ("POST",   "/api/agent/run"),
    ("POST",   "/api/generate"),
}


def _registered() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        for m in methods - {"HEAD"}:
            out.add((m, getattr(r, "path", "")))
    return out


def test_app_boots() -> None:
    assert app.title == "AI Job-CV Matching Agent"


def test_all_expected_routes_registered() -> None:
    registered = _registered()
    missing = EXPECTED_ROUTES - registered
    assert not missing, f"missing routes: {sorted(missing)}"


def test_cors_middleware_present() -> None:
    types = [type(m).__name__ for m in getattr(app, "user_middleware", [])]
    text = " ".join(types) + " " + " ".join(repr(m) for m in app.user_middleware)
    assert "CORS" in text or "cors" in text.lower()


def _run_all() -> None:
    tests = [test_app_boots, test_all_expected_routes_registered, test_cors_middleware_present]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
