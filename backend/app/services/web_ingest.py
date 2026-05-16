"""Fetch + extract content from external URLs the user adds as
library sources.

Two flavours:

  ingest_web(url)         → fetch HTML, strip nav/footer via trafilatura,
                            ask LLM (when enabled) to pull
                            projects/skills/bio into structured dict.
  ingest_github(url)      → resolve to a user or repo. For users, list
                            public repos via GitHub REST API (no auth
                            needed for low volumes). For repos, fetch
                            README and metadata.

Both return (raw_text, extracted_dict, error_str). On failure,
`extracted` is `{}` and `error` is human-readable so the UI can show
it next to the failed source row.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("ai_job_cv_matcher.web_ingest")

DEFAULT_USER_AGENT = "ApplyAgent/0.1 (+https://github.com/danialza/ApplyAgent-AI)"
HTTP_TIMEOUT = 20.0
GITHUB_API_BASE = "https://api.github.com"
MAX_REPOS = 30
MAX_README_CHARS = 8000
MAX_WEB_TEXT_CHARS = 12000


# ---------- Public entry points ----------

def detect_kind(url: str) -> str:
    """Classify a URL into the WebSource.kind taxonomy."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return "web"
    if host.endswith("github.com"):
        path = urlparse(url).path.strip("/")
        if not path:
            return "web"
        parts = path.split("/")
        if len(parts) == 1:
            return "github_user"
        return "github_repo"
    return "web"


def ingest(url: str) -> tuple[str, str, dict, str]:
    """Single entry point. Returns (kind, raw_text, extracted, error)."""
    kind = detect_kind(url)
    if kind == "github_user":
        raw, ext, err = _ingest_github_user(url)
    elif kind == "github_repo":
        raw, ext, err = _ingest_github_repo(url)
    else:
        raw, ext, err = _ingest_web(url)
    return kind, raw, ext, err


# ---------- Generic web ----------

def _ingest_web(url: str) -> tuple[str, dict, str]:
    try:
        import httpx
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        if resp.status_code >= 400:
            return "", {}, f"HTTP {resp.status_code} from {url}"
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        return "", {}, f"fetch failed: {exc}"

    text = _strip_html(html)[:MAX_WEB_TEXT_CHARS]
    if not text.strip():
        return "", {}, "empty content (scraper returned nothing)"

    extracted = _llm_extract_portfolio(text, url) or {}
    return text, extracted, ""


def _strip_html(html: str) -> str:
    """Use trafilatura when available (best at boilerplate removal),
    fall back to a crude bs4 .get_text() so the import path doesn't
    explode in dev environments missing trafilatura."""
    try:
        import trafilatura
        out = trafilatura.extract(
            html, include_comments=False, include_tables=False, include_links=False
        )
        if out:
            return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("trafilatura failed (%s); falling back to bs4", exc)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n").strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("html strip failed: %s", exc)
        return html


def _llm_extract_portfolio(text: str, url: str) -> dict | None:
    """Ask the LLM to pull projects / skills / bio from a portfolio
    page. Returns None when LLM is off or the call fails.

    Output schema (loose; library builder takes what it can):
      {
        "bio": str,
        "skills": [str, ...],
        "projects": [{"title": str, "summary": str, "tags": [str]}, ...]
      }
    """
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        return None

    system = (
        "Extract a candidate's portfolio data from web page text. Output "
        "one JSON object with these keys:\n"
        "  bio: 2-3 sentence summary about the person\n"
        "  skills: list of distinct skill / tool tokens visible in the text\n"
        "  projects: list of objects {title, summary, tags} for every "
        "project, side project, talk, or open-source work referenced\n"
        "Rules: never invent. If the page has no info for a key, return "
        "an empty value. Cap projects at 12. Tag each project with 3-6 "
        "tech tokens from the surrounding text."
    )
    user = json.dumps({"url": url, "text": text}, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("portfolio LLM extract failed for %s: %s", url, exc)
        return None


# ---------- GitHub ----------

def _ingest_github_user(url: str) -> tuple[str, dict, str]:
    user = urlparse(url).path.strip("/").split("/")[0]
    if not user:
        return "", {}, "could not parse GitHub username from URL"
    try:
        import httpx
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            # Public-API limit is 60/h unauthenticated — fine for one
            # user's repos.
            resp = client.get(
                f"{GITHUB_API_BASE}/users/{user}/repos",
                params={"per_page": MAX_REPOS, "sort": "updated"},
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/vnd.github+json"},
            )
        if resp.status_code >= 400:
            return "", {}, f"GitHub API {resp.status_code}: {resp.text[:200]}"
        repos = resp.json()
    except Exception as exc:  # noqa: BLE001
        return "", {}, f"GitHub fetch failed: {exc}"

    projects: list[dict] = []
    for r in repos[:MAX_REPOS]:
        if r.get("fork"):
            # Skip forks — they're not really the user's authored work.
            continue
        summary = (r.get("description") or "").strip()
        tags: list[str] = []
        if r.get("language"):
            tags.append(r["language"])
        tags.extend(r.get("topics") or [])
        projects.append({
            "title": r.get("name") or "",
            "summary": summary,
            "tags": tags[:6],
            "url": r.get("html_url") or "",
            "stars": r.get("stargazers_count", 0),
        })

    raw_lines = [f"GitHub user @{user} ({len(projects)} non-fork repos)"]
    for p in projects:
        raw_lines.append(f"- {p['title']}: {p['summary']} [{', '.join(p['tags'])}]")
    return "\n".join(raw_lines), {"projects": projects, "bio": "", "skills": []}, ""


def _ingest_github_repo(url: str) -> tuple[str, dict, str]:
    path = urlparse(url).path.strip("/").split("/")
    if len(path) < 2:
        return "", {}, "could not parse owner/repo from URL"
    owner, repo = path[0], path[1]
    try:
        import httpx
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            meta = client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/vnd.github+json"},
            )
            if meta.status_code >= 400:
                return "", {}, f"GitHub API {meta.status_code}: {meta.text[:200]}"
            meta_json = meta.json()
            readme = client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/readme",
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/vnd.github.raw"},
            )
            readme_text = readme.text if readme.status_code < 400 else ""
    except Exception as exc:  # noqa: BLE001
        return "", {}, f"GitHub fetch failed: {exc}"

    tags: list[str] = []
    if meta_json.get("language"):
        tags.append(meta_json["language"])
    tags.extend(meta_json.get("topics") or [])
    project = {
        "title": meta_json.get("name") or repo,
        "summary": (meta_json.get("description") or "").strip(),
        "tags": tags[:6],
        "url": meta_json.get("html_url") or url,
        "stars": meta_json.get("stargazers_count", 0),
    }
    raw_text = (
        f"GitHub repo {owner}/{repo}\n"
        f"Description: {project['summary']}\n"
        f"Topics: {', '.join(tags)}\n\n"
        f"--- README ---\n{readme_text[:MAX_README_CHARS]}"
    )
    return raw_text, {"projects": [project], "bio": "", "skills": tags}, ""
