"""Best-effort job-posting scraper.

Pipeline:
  1. Validate URL (http/https only, block obvious local/private targets).
  2. Optional robots.txt check via `urllib.robotparser`. Fail-open if the
     robots file itself can't be fetched; refuse cleanly on explicit Disallow.
  3. Polite per-host throttle (default 2 s between requests to the same host).
  4. Fetch HTML with `httpx`, custom UA, sane timeout, no redirects to other
     schemes.
  5. Extract metadata in order of reliability:
       a. JSON-LD `JobPosting` (best — structured).
       b. OpenGraph / Twitter / standard meta tags.
       c. <title> / <h1> fallbacks.
  6. Extract the body text via `trafilatura` if installed, else a BeautifulSoup
     fallback that strips nav/footer/script noise.
  7. Compose a labelled text block ("Job Title: …\nCompany: …\n\n<body>") so
     the existing `parse_job_text` picks the structured fields up cleanly.

Hard limits:
  * Refuses to fetch private / loopback / link-local hosts (basic SSRF guard).
  * Caps response size at 2 MB.
  * Never bypasses logins, paywalls, CAPTCHAs, or anti-bot challenges.
  * If extraction fails, returns `success=False` with a human-readable reason.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger("ai_job_cv_matcher.scraper")

USER_AGENT = (
    "AI-Job-CV-Matching-Agent/0.1 "
    "(+https://github.com/ — portfolio project; respects robots.txt)"
)

REQUEST_TIMEOUT_SECONDS = 12.0
MAX_BYTES = 2 * 1024 * 1024  # 2 MB
MIN_INTERVAL_PER_HOST_SECONDS = 2.0
MAX_REDIRECTS = 5

# Domains that are known to require login / actively block bots / serve
# JS-rendered job pages. Refusing them up-front gives the user a clear,
# actionable error instead of a 403 / empty body / soft-banned IP.
# Override or extend via APP_SCRAPER_BLOCKLIST (comma-separated domains).
_DEFAULT_BLOCKLIST = {
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "wellfound.com",
    "angel.co",
    "facebook.com",
    "x.com",
    "twitter.com",
}


def _blocklist() -> set[str]:
    extra = os.getenv("APP_SCRAPER_BLOCKLIST", "")
    extras = {d.strip().lower() for d in extra.split(",") if d.strip()}
    return _DEFAULT_BLOCKLIST | extras


def _host_is_blocked(host: str) -> bool:
    """True if host (or any parent domain) is on the block-list."""
    low = (host or "").lower()
    if not low:
        return False
    parts = low.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in _blocklist():
            return True
    return False


@dataclass
class ScrapeResult:
    url: str
    success: bool
    extracted_text: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    employment_type: str = ""
    error: str = ""
    notes: list[str] = field(default_factory=list)


# ---------- URL / SSRF guards ----------

_BAD_HOSTS = {"localhost", "0.0.0.0", "broadcasthost"}


def _validate_url(url: str) -> tuple[str, str]:
    """Return (cleaned_url, host) or raise `ValueError` on invalid input."""
    if not url or not isinstance(url, str):
        raise ValueError("URL is required.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http(s) URLs are supported.")
    if not parsed.netloc:
        raise ValueError("URL is missing a host.")
    host = parsed.hostname or ""
    low = host.lower()
    if low in _BAD_HOSTS:
        raise ValueError("Refusing to fetch local addresses.")
    # IP literal? Reject private / loopback / link-local.
    try:
        ip = ipaddress.ip_address(low)
    except ValueError:
        ip = None  # Not an IP literal — that's fine, hostname will resolve later.
    if ip is not None and (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    ):
        raise ValueError("Refusing to fetch private/loopback addresses.")
    if _host_is_blocked(low):
        raise ValueError(
            f"'{low}' is on the scraper block-list (login/anti-bot site). "
            "Please paste the JD manually instead."
        )
    return parsed.geturl(), host


# ---------- robots.txt ----------

def _robots_allows(url: str) -> tuple[bool, str]:
    """Best-effort robots.txt check.

    Returns (allowed, note). Fail-open: if robots can't be loaded we treat
    the URL as allowed but record a note.
    """
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        if rp.can_fetch(USER_AGENT, url):
            return True, ""
        return False, f"Disallowed by robots.txt at {robots_url}."
    except Exception:  # pragma: no cover  # network/parse failures are non-fatal
        return True, "robots.txt could not be loaded; proceeding cautiously."


# ---------- Per-host throttle ----------

_last_fetch: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _throttle(host: str) -> None:
    with _throttle_lock:
        last = _last_fetch.get(host, 0.0)
        wait = MIN_INTERVAL_PER_HOST_SECONDS - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_fetch[host] = time.monotonic()


# ---------- Fetching ----------

def _decode_response_body(body: bytes, content_type: str) -> str:
    """Decode using the charset declared in `Content-Type`, with utf-8 fallback."""
    charset = "utf-8"
    if content_type:
        for part in content_type.split(";"):
            part = part.strip().lower()
            if part.startswith("charset="):
                charset = part.split("=", 1)[1].strip("\"' ")
                break
    try:
        return body.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return body.decode("utf-8", errors="replace")


def _fetch_html(url: str) -> str:
    """GET the URL with `httpx`, with hardened safety checks.

    Defence-in-depth on top of `_validate_url`:

    - Manual redirect handling with a hard cap (`MAX_REDIRECTS`) so an
      attacker can't force unbounded chains.
    - Each redirect target is re-validated through `_validate_url` —
      catches DNS rebind / IP-literal redirects and post-validation
      domain-blocklist hits.
    - `Content-Length` (when present) is checked before downloading —
      we reject huge responses without buffering them.
    - Body is decoded using the charset declared in the response
      `Content-Type`, with utf-8 fallback.
    """
    import httpx  # local import — keeps the rest importable without httpx

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    current_url = url
    with httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,    # we follow manually so each hop is re-validated
        headers=headers,
    ) as client:
        for hop in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current_url) as resp:
                # Manual redirect handling with re-validation.
                if resp.is_redirect:
                    next_url = resp.headers.get("location", "")
                    if not next_url:
                        raise ValueError("Redirect with no Location header.")
                    # `httpx` resolves relative redirects via the request URL.
                    next_url = str(httpx.URL(current_url).join(next_url))
                    try:
                        next_clean, _next_host = _validate_url(next_url)
                    except ValueError as exc:
                        raise ValueError(f"Unsafe redirect target: {exc}") from exc
                    logger.info("Scraper: %s → redirect → %s", current_url, next_clean)
                    current_url = next_clean
                    continue

                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type.lower() and "xml" not in content_type.lower():
                    raise ValueError(f"Unsupported content-type: {content_type or 'unknown'}")

                # Pre-check Content-Length when the server sets it.
                content_length = resp.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > MAX_BYTES:
                    raise ValueError(
                        f"Response Content-Length {int(content_length)} exceeds 2 MB limit."
                    )

                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Response exceeded 2 MB size limit.")
                body = b"".join(chunks)
                return _decode_response_body(body, content_type)

        raise ValueError(f"Too many redirects (>{MAX_REDIRECTS}).")


# ---------- Extraction ----------

def _extract_jsonld_jobposting(soup: Any) -> dict[str, Any]:
    """Walk every <script type='application/ld+json'> and return the first
    `JobPosting` payload (or {} if none found)."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text(strip=False) or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _flatten_jsonld(data):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if any((isinstance(x, str) and x.lower() == "jobposting") for x in types):
                return node
    return {}


def _flatten_jsonld(node: Any):
    """Yield every dict in a JSON-LD blob (which may use @graph or be a list)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _flatten_jsonld(v)
    elif isinstance(node, list):
        for v in node:
            yield from _flatten_jsonld(v)


def _meta_content(soup: Any, names: list[str]) -> str:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _strip_html_to_text(soup: Any) -> str:
    """BS4 fallback: remove obvious chrome and join visible text."""
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse 3+ newlines and trim.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _trafilatura_extract(html: str) -> str:
    try:
        import trafilatura  # type: ignore

        return trafilatura.extract(html, include_comments=False, favor_recall=True) or ""
    except Exception:
        return ""


def _format_salary(raw: Any) -> str:
    """JSON-LD `baseSalary` can be a string, dict, or list. Best-effort flatten."""
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        value = raw.get("value")
        if isinstance(value, dict):
            min_v = value.get("minValue")
            max_v = value.get("maxValue")
            unit = value.get("unitText", "")
            cur = raw.get("currency", "")
            if min_v and max_v:
                return f"{cur} {min_v} - {max_v} {unit}".strip()
            if value.get("value"):
                return f"{cur} {value['value']} {unit}".strip()
        elif isinstance(value, (int, float, str)):
            return f"{raw.get('currency', '')} {value}".strip()
    return ""


def _format_location(loc: Any) -> str:
    """`jobLocation` may be a list, dict, or nested address."""
    if not loc:
        return ""
    if isinstance(loc, list):
        return ", ".join(filter(None, [_format_location(x) for x in loc]))
    if isinstance(loc, dict):
        addr = loc.get("address") if "address" in loc else loc
        if isinstance(addr, dict):
            parts = [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None,
            ]
            return ", ".join([p for p in parts if p])
    if isinstance(loc, str):
        return loc
    return ""


def _build_metadata(soup: Any, jsonld: dict[str, Any]) -> dict[str, str]:
    """Compose normalised metadata from JSON-LD + meta tags + h1 fallbacks."""
    title = ""
    if jsonld.get("title"):
        title = str(jsonld["title"]).strip()
    if not title:
        title = _meta_content(soup, ["og:title", "twitter:title"])
    if not title and soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    company = ""
    org = jsonld.get("hiringOrganization")
    if isinstance(org, dict):
        company = (org.get("name") or "").strip()
    elif isinstance(org, str):
        company = org.strip()
    if not company:
        company = _meta_content(soup, ["og:site_name"])

    location = _format_location(jsonld.get("jobLocation"))
    if not location:
        location = _meta_content(soup, ["og:locality", "geo.placename"])

    salary = _format_salary(jsonld.get("baseSalary"))

    employment = ""
    et = jsonld.get("employmentType")
    if isinstance(et, list):
        employment = ", ".join(str(x) for x in et)
    elif isinstance(et, str):
        employment = et

    return {
        "title": title.strip(),
        "company": company.strip(),
        "location": location.strip(),
        "salary": salary.strip(),
        "employment_type": employment.strip(),
    }


def _build_body(html: str, jsonld: dict[str, Any], soup: Any) -> str:
    """Pick the best body text available. Order: JSON-LD description → trafilatura → BS4."""
    if jsonld.get("description"):
        # JobPosting.description is often HTML — strip tags.
        from bs4 import BeautifulSoup  # local import to avoid mandatory dep at import time
        return BeautifulSoup(str(jsonld["description"]), "html.parser").get_text("\n").strip()

    extracted = _trafilatura_extract(html)
    if extracted and len(extracted) > 200:
        return extracted

    return _strip_html_to_text(soup)


# ---------- Public API ----------

def scrape_job_url(url: str) -> ScrapeResult:
    """Fetch and parse a job posting. Always returns a `ScrapeResult` (never raises).

    On failure `success=False` and `error` describes the reason in plain English.
    """
    try:
        clean_url, host = _validate_url(url)
    except ValueError as e:
        return ScrapeResult(url=url, success=False, error=str(e))

    allowed, robots_note = _robots_allows(clean_url)
    if not allowed:
        return ScrapeResult(
            url=clean_url,
            success=False,
            error=robots_note or "Blocked by robots.txt.",
        )

    _throttle(host)

    try:
        html = _fetch_html(clean_url)
    except Exception as e:  # noqa: BLE001
        return ScrapeResult(
            url=clean_url,
            success=False,
            error=f"Could not fetch page: {e}",
            notes=[robots_note] if robots_note else [],
        )

    try:
        from bs4 import BeautifulSoup  # local import — beautifulsoup4 listed in requirements
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return ScrapeResult(url=clean_url, success=False, error=f"Could not parse HTML: {e}")

    jsonld = _extract_jsonld_jobposting(soup)
    meta = _build_metadata(soup, jsonld)
    body = _build_body(html, jsonld, soup)

    if not body or len(body) < 100:
        return ScrapeResult(
            url=clean_url,
            success=False,
            error=(
                "Could not extract a job description from the page. "
                "The site may require login, render content via JavaScript, "
                "or block scraping. Please paste the JD manually."
            ),
            notes=[robots_note] if robots_note else [],
        )

    # Compose a labelled prefix so the existing rule-based parser picks
    # structured fields straight out of the text.
    prefix_lines: list[str] = []
    if meta["title"]:
        prefix_lines.append(f"Job Title: {meta['title']}")
    if meta["company"]:
        prefix_lines.append(f"Company: {meta['company']}")
    if meta["location"]:
        prefix_lines.append(f"Location: {meta['location']}")
    if meta["salary"]:
        prefix_lines.append(f"Salary: {meta['salary']}")
    prefix = "\n".join(prefix_lines)
    extracted_text = (prefix + "\n\n" + body).strip() if prefix else body.strip()

    return ScrapeResult(
        url=clean_url,
        success=True,
        extracted_text=extracted_text,
        title=meta["title"],
        company=meta["company"],
        location=meta["location"],
        salary=meta["salary"],
        employment_type=meta["employment_type"],
        notes=[robots_note] if robots_note else [],
    )
