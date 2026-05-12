"""Optional LLM-based extraction layer.

Calls an OpenAI-compatible Chat Completions endpoint to convert raw CV / JD
text into structured JSON. Validates the response via Pydantic and converts
it back to the project's `ParsedCV` / `ParsedJob` dataclasses.

Always optional:
  * `USE_LLM_EXTRACTION` must be truthy AND `OPENAI_API_KEY` must be set.
  * Any failure (no key, network error, timeout, non-JSON output, schema
    violation) returns `None` — the caller is expected to fall back to the
    rule-based parser.

Environment:
  USE_LLM_EXTRACTION   - "true" / "1" / "yes" enables the path.
  OPENAI_API_KEY       - API key (never logged).
  OPENAI_BASE_URL      - optional base URL; default https://api.openai.com/v1
  LLM_MODEL_NAME       - optional model name; default gpt-4o-mini
  LLM_TIMEOUT_SECONDS  - optional, default 30
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.cv_parser import ParsedCV
from app.services.job_parser import ParsedJob

logger = logging.getLogger("ai_job_cv_matcher.llm")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0


# ---------- Pydantic schemas for LLM output ----------

class _LLMContact(BaseModel):
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


class _LLMCv(BaseModel):
    name: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    contact: _LLMContact = Field(default_factory=_LLMContact)


class _LLMJob(BaseModel):
    job_title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    employment_type: str = ""
    remote_type: str = ""
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    experience_level: str = ""
    education_requirements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)


# ---------- Prompt templates ----------

_CV_SYSTEM_PROMPT = (
    "You are a precise resume parser. Extract structured data from the CV "
    "the user provides. Output ONLY valid JSON matching the schema. "
    "Do not invent skills or experience that are not in the source text."
)

_CV_USER_PROMPT = """\
Extract structured data from this CV text. Return ONLY valid JSON with this exact shape:

{{
  "name": "",
  "summary": "",
  "skills": [],
  "education": [],
  "experience": [],
  "projects": [],
  "certifications": [],
  "languages": [],
  "contact": {{
    "email": "",
    "phone": "",
    "linkedin": "",
    "github": "",
    "website": ""
  }}
}}

Rules:
- Use empty strings / empty arrays when a field is not present in the CV.
- "experience" / "projects" / "education" / "certifications" should each be a list of short bullet-style strings (one per role / project / degree / cert).
- "skills" should be a list of canonical skill names (e.g. "Python", "FastAPI", "Machine Learning"). Do NOT include sentences.
- Do NOT include any prose outside the JSON. Do NOT wrap the JSON in markdown.

CV TEXT:
\"\"\"
{text}
\"\"\"
"""


_JOB_SYSTEM_PROMPT = (
    "You are a precise job-description parser. Extract structured data from "
    "the JD the user provides. Output ONLY valid JSON matching the schema. "
    "Do not invent requirements that are not in the source text."
)

_JOB_USER_PROMPT = """\
Extract structured data from this job description. Return ONLY valid JSON with this exact shape:

{{
  "job_title": "",
  "company": "",
  "location": "",
  "salary": "",
  "employment_type": "",
  "remote_type": "",
  "required_skills": [],
  "preferred_skills": [],
  "responsibilities": [],
  "qualifications": [],
  "experience_level": "",
  "education_requirements": [],
  "technologies": [],
  "soft_skills": []
}}

Rules:
- "experience_level" must be one of: "internship" | "junior" | "mid-level" | "senior" | "lead" | "principal" | "" (empty if unclear).
- "employment_type" should be e.g. "full-time" / "part-time" / "contract" / "internship" / "" if unclear.
- "remote_type" should be one of "remote" / "hybrid" / "on-site" / "" if unclear.
- "required_skills" / "preferred_skills" / "technologies" must be canonical skill names (e.g. "Python", "FastAPI", "Machine Learning"). No sentences.
- "soft_skills" should list short canonical names (e.g. "Communication", "Mentoring").
- Use empty strings / empty arrays when a field is not present.
- Do NOT include any prose outside the JSON. Do NOT wrap the JSON in markdown.

JOB DESCRIPTION:
\"\"\"
{text}
\"\"\"
"""


# ---------- Config ----------

def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """True iff `USE_LLM_EXTRACTION` is truthy AND an API key is present."""
    if not _truthy(os.getenv("USE_LLM_EXTRACTION")):
        return False
    return bool(os.getenv("OPENAI_API_KEY"))


def _config() -> dict[str, Any]:
    return {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": (os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "model": os.getenv("LLM_MODEL_NAME") or DEFAULT_MODEL,
        "timeout": float(os.getenv("LLM_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT),
    }


# ---------- LLM call ----------

# Indirection so tests can monkeypatch the network layer without httpx.
def _chat_completion(messages: list[dict[str, str]]) -> str:
    """POST to /chat/completions and return the assistant message text.

    Uses `response_format={"type": "json_object"}` to encourage strict JSON
    output on supporting models. Raises on any non-2xx / network failure.
    """
    import httpx  # local import — keeps module importable when httpx absent

    cfg = _config()
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=cfg["timeout"]) as client:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            # Surface the upstream error body so the user sees WHICH 400:
            # "invalid API key", "model not found", "json_object not
            # supported", etc. instead of an opaque httpx exception.
            try:
                err = resp.json().get("error", {})
                msg = err.get("message") or err.get("type") or resp.text[:300]
            except Exception:  # noqa: BLE001
                msg = resp.text[:300]
            raise RuntimeError(
                f"{resp.status_code} from {url} — {msg}"
            )
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def _coerce_json(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON parser. Tries the raw string, then strips code fences."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    # Strip ```json ... ``` fences if the model added them.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fence:
        try:
            out = json.loads(fence.group(1))
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ---------- Public API ----------

def extract_cv(text: str) -> ParsedCV | None:
    """Run the LLM CV-extraction prompt; return `None` on any failure."""
    if not is_enabled() or not (text or "").strip():
        return None
    messages = [
        {"role": "system", "content": _CV_SYSTEM_PROMPT},
        {"role": "user", "content": _CV_USER_PROMPT.format(text=text.strip())},
    ]
    try:
        raw = _chat_completion(messages)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM CV extraction failed: %s", e)
        return None

    payload = _coerce_json(raw)
    if payload is None:
        logger.warning("LLM CV extraction returned non-JSON output; falling back.")
        return None

    try:
        validated = _LLMCv.model_validate(payload)
    except ValidationError as e:
        logger.warning("LLM CV output failed schema validation: %s", e.errors())
        return None

    return ParsedCV(
        name=validated.name,
        summary=validated.summary,
        skills=validated.skills,
        education=validated.education,
        experience=validated.experience,
        projects=validated.projects,
        certifications=validated.certifications,
        languages=validated.languages,
        email=validated.contact.email,
        phone=validated.contact.phone,
        linkedin=validated.contact.linkedin,
        github=validated.contact.github,
        portfolio=validated.contact.website,
    )


def extract_job(text: str) -> ParsedJob | None:
    """Run the LLM JD-extraction prompt; return `None` on any failure."""
    if not is_enabled() or not (text or "").strip():
        return None
    messages = [
        {"role": "system", "content": _JOB_SYSTEM_PROMPT},
        {"role": "user", "content": _JOB_USER_PROMPT.format(text=text.strip())},
    ]
    try:
        raw = _chat_completion(messages)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM JD extraction failed: %s", e)
        return None

    payload = _coerce_json(raw)
    if payload is None:
        logger.warning("LLM JD extraction returned non-JSON output; falling back.")
        return None

    try:
        validated = _LLMJob.model_validate(payload)
    except ValidationError as e:
        logger.warning("LLM JD output failed schema validation: %s", e.errors())
        return None

    return ParsedJob(
        job_title=validated.job_title,
        company=validated.company,
        location=validated.location,
        salary=validated.salary,
        employment_type=validated.employment_type,
        remote_type=validated.remote_type,
        required_skills=validated.required_skills,
        preferred_skills=validated.preferred_skills,
        responsibilities=validated.responsibilities,
        qualifications=validated.qualifications,
        experience_level=validated.experience_level,
        education_requirements=validated.education_requirements,
        technologies=validated.technologies,
        soft_skills=validated.soft_skills,
        raw_text=text,
    )
