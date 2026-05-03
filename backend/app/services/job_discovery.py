"""Discover jobs from **public, no-login JSON APIs** only.

Sources (all free, no auth required):

  * **RemoteOK**       — `https://remoteok.com/api`
  * **Remotive**       — `https://remotive.com/api/remote-jobs`
  * **HN "Who is Hiring"** via Algolia
    (`https://hn.algolia.com/api/v1/search`, querying recent stories
    matching the user's terms).

Hard guarantees (constraints from the spec):
  * No HTML scraping of job boards. Only documented JSON endpoints.
  * No login flows, no headless browsers, no CAPTCHA bypass.
  * One descriptive `User-Agent` so the upstream services can identify
    and block us if they ever choose to.
  * Per-host throttle (3 s minimum gap) reusing the same lock pattern
    as `job_scraper`.
  * Each source isolated: a failure in one returns an entry in
    `errors` and the call still succeeds with the others.
  * Total result count is hard-capped (`max_total ≤ 100`) so a runaway
    request can't fan out into thousands of upstream calls.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger("ai_job_cv_matcher.discovery")

USER_AGENT = (
    "AI-Job-CV-Matching-Agent/0.1 "
    "(+https://github.com/ — portfolio project, public APIs only)"
)
REQUEST_TIMEOUT = 10.0
MIN_INTERVAL_PER_HOST = 3.0
MAX_TOTAL_HARD_CAP = 100

_throttle_lock = threading.Lock()
_last_fetch: dict[str, float] = {}


def _throttle(host: str) -> None:
    with _throttle_lock:
        last = _last_fetch.get(host, 0.0)
        wait = MIN_INTERVAL_PER_HOST - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_fetch[host] = time.monotonic()


# ---------- Common shape ----------

@dataclass
class DiscoveredJob:
    """Normalised job record returned by every source."""
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    snippet: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""
    posted_at: str = ""  # ISO-8601 when known
    relevance_score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "snippet": self.snippet,
            "tags": self.tags,
            "source": self.source,
            "posted_at": self.posted_at,
            "relevance_score": round(self.relevance_score, 4),
            "matched_terms": self.matched_terms,
        }


# ---------- HTTP helper (test seam) ----------

def _http_get_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    """GET JSON. Indirection so tests can monkeypatch a single function."""
    import httpx  # local — keeps the rest importable when httpx absent

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        resp = client.get(url, params=params or None)
        resp.raise_for_status()
        return resp.json()


# ---------- Sources ----------

def _safe_strip_html(html_or_text: str) -> str:
    """Cheap HTML → plain text. Avoids importing BS4 just for snippets."""
    if not html_or_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", html_or_text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:400]


def fetch_remoteok(limit: int) -> list[DiscoveredJob]:
    _throttle("remoteok.com")
    payload = _http_get_json("https://remoteok.com/api")
    out: list[DiscoveredJob] = []
    # RemoteOK's first list element is a metadata blob; skip dicts that
    # lack the standard job keys.
    for item in payload[:limit + 5] if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        if "position" not in item and "title" not in item:
            continue
        out.append(DiscoveredJob(
            title=item.get("position") or item.get("title") or "",
            company=item.get("company") or "",
            location=item.get("location") or "Remote",
            url=item.get("url") or item.get("apply_url") or "",
            snippet=_safe_strip_html(item.get("description") or ""),
            tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
            source="remoteok",
            posted_at=item.get("date") or "",
        ))
        if len(out) >= limit:
            break
    return out


def fetch_remotive(limit: int, query: str | None = None) -> list[DiscoveredJob]:
    _throttle("remotive.com")
    params = {"limit": str(limit)}
    if query:
        params["search"] = query
    payload = _http_get_json("https://remotive.com/api/remote-jobs", params=params)
    out: list[DiscoveredJob] = []
    for item in (payload.get("jobs") or []) if isinstance(payload, dict) else []:
        out.append(DiscoveredJob(
            title=item.get("title") or "",
            company=item.get("company_name") or "",
            location=item.get("candidate_required_location") or "Remote",
            url=item.get("url") or "",
            snippet=_safe_strip_html(item.get("description") or ""),
            tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
            source="remotive",
            posted_at=item.get("publication_date") or "",
        ))
        if len(out) >= limit:
            break
    return out


def fetch_hn_who_is_hiring(query: str, limit: int) -> list[DiscoveredJob]:
    """Search HN comments via Algolia for jobs matching the query.

    Algolia's HN endpoint is documented and free; queries against
    `tags=comment` return individual job postings inside the monthly
    "Who is hiring?" thread.
    """
    _throttle("hn.algolia.com")
    params = {
        "query": query,
        "tags": "comment,story_36932099",  # filter to comment objects
        "hitsPerPage": str(min(limit, 50)),
    }
    # Use the broader endpoint without the story filter — Algolia returns
    # the latest matching comments across all "Who is hiring" threads.
    params = {"query": query, "tags": "comment", "hitsPerPage": str(min(limit, 50))}
    payload = _http_get_json("https://hn.algolia.com/api/v1/search", params=params)
    out: list[DiscoveredJob] = []
    for hit in (payload.get("hits") or []) if isinstance(payload, dict) else []:
        text = _safe_strip_html(hit.get("comment_text") or "")
        if not text:
            continue
        # Only keep comments that look like job posts.
        low = text.lower()
        if "hiring" not in low and "remote" not in low and "engineer" not in low:
            continue
        story_id = hit.get("story_id") or hit.get("objectID")
        url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}" if hit.get("objectID") else ""
        # Best-effort first-line title.
        first_line = (text.split(" - ")[0] if " - " in text[:120] else text)[:120]
        out.append(DiscoveredJob(
            title=first_line,
            company="",
            location="",
            url=url,
            snippet=text[:500],
            tags=[],
            source="hn-who-is-hiring",
            posted_at=hit.get("created_at") or "",
        ))
        if len(out) >= limit:
            break
    return out


# Registry. Each entry returns up to `limit` records for the given query.
_SOURCES: dict[str, callable] = {
    "remoteok": lambda limit, query=None: fetch_remoteok(limit),
    "remotive": lambda limit, query=None: fetch_remotive(limit, query),
    "hn": lambda limit, query=None: fetch_hn_who_is_hiring(query or "engineer", limit),
}


# ---------- Relevance scoring ----------

_TOKEN_RE = re.compile(r"[A-Za-z0-9\+\#\.]{2,}")


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _score_job(
    job: DiscoveredJob,
    *,
    skill_terms: set[str],
    role_terms: set[str],
    domain_terms: set[str],
) -> tuple[float, list[str]]:
    """Simple overlap score: skills 0.6, roles 0.3, domains 0.1.

    Cheap and explainable. Embedding-based scoring can replace this
    later via the same return type.
    """
    haystack_text = " ".join([job.title, job.company, job.snippet, " ".join(job.tags)])
    haystack = _tokenize(haystack_text)
    haystack |= {t.lower() for t in job.tags}

    matched: list[str] = []
    skill_hits = 0
    for t in skill_terms:
        if t.lower() in haystack:
            skill_hits += 1
            matched.append(t)
    role_hits = 0
    for t in role_terms:
        if any(part in haystack for part in t.lower().split()):
            role_hits += 1
            if t not in matched:
                matched.append(t)
    domain_hits = 0
    for t in domain_terms:
        if t.lower() in haystack:
            domain_hits += 1
            if t not in matched:
                matched.append(t)

    skill_score = (skill_hits / len(skill_terms)) if skill_terms else 0.0
    role_score = (role_hits / len(role_terms)) if role_terms else 0.0
    domain_score = (domain_hits / len(domain_terms)) if domain_terms else 0.0
    return 0.6 * skill_score + 0.3 * role_score + 0.1 * domain_score, matched


# ---------- Public API ----------

@dataclass
class DiscoveryResult:
    queries_used: list[str]
    results: list[DiscoveredJob]
    skipped_sources: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def discover_jobs(
    *,
    queries: Iterable[str],
    tags: dict[str, list[str]] | None = None,
    sources: list[str] | None = None,
    max_per_source: int = 25,
    max_total: int = 50,
) -> DiscoveryResult:
    """Run every selected source and return ranked, deduped jobs.

    Args:
        queries: free-text queries (typically from `query_builder`).
        tags: dict with optional `roles` / `skills` / `tools` / `domains`
            keys. Used both to derive search terms and to score results.
        sources: subset of {"remoteok", "remotive", "hn"}. Default all.
        max_per_source: cap per upstream source.
        max_total: cap on the final returned list (≤ MAX_TOTAL_HARD_CAP).
    """
    queries_list = [q for q in (queries or []) if q]
    sources = sources or list(_SOURCES.keys())
    max_total = min(max_total, MAX_TOTAL_HARD_CAP)
    max_per_source = max(1, min(max_per_source, MAX_TOTAL_HARD_CAP))

    skill_terms: set[str] = set((tags or {}).get("skills") or [])
    role_terms: set[str] = set((tags or {}).get("roles") or [])
    domain_terms: set[str] = set((tags or {}).get("domains") or [])

    fetched: dict[str, DiscoveredJob] = {}  # url → job (dedup key)
    errors: list[dict[str, str]] = []
    skipped: list[str] = []

    primary_query = queries_list[0] if queries_list else ""

    for src in sources:
        fn = _SOURCES.get(src)
        if fn is None:
            skipped.append(src)
            errors.append({"source": src, "error": "unknown source"})
            continue
        try:
            jobs = fn(max_per_source, primary_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Discovery source %s failed: %s", src, exc)
            errors.append({"source": src, "error": str(exc)})
            continue
        for j in jobs:
            key = (j.url or f"{j.source}:{j.title}:{j.company}").lower()
            if key in fetched:
                continue
            fetched[key] = j

    # Score + sort.
    scored: list[DiscoveredJob] = []
    for j in fetched.values():
        score, matched = _score_job(
            j, skill_terms=skill_terms, role_terms=role_terms, domain_terms=domain_terms,
        )
        j.relevance_score = score
        j.matched_terms = matched
        scored.append(j)

    scored.sort(key=lambda x: (-x.relevance_score, x.source, x.title))
    return DiscoveryResult(
        queries_used=queries_list,
        results=scored[:max_total],
        skipped_sources=skipped,
        errors=errors,
    )
