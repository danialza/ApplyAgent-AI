"""Sample / unit-test-style checks for the LLM extraction layer.

No real network calls — `_chat_completion` is monkeypatched per test.

    python -m tests.test_llm_extraction
"""
from __future__ import annotations

import os

from app.services import llm_extraction_service as llm
from app.services import extraction


# ---------- helpers ----------

def _enable(api_key: str = "test-key") -> None:
    os.environ["USE_LLM_EXTRACTION"] = "true"
    os.environ["OPENAI_API_KEY"] = api_key


def _disable() -> None:
    os.environ.pop("USE_LLM_EXTRACTION", None)
    os.environ.pop("OPENAI_API_KEY", None)


def _patch_chat(response: str | Exception) -> None:
    def fake(messages):  # noqa: ARG001
        if isinstance(response, Exception):
            raise response
        return response
    llm._chat_completion = fake  # type: ignore[assignment]


# ---------- gating ----------

def test_disabled_when_flag_off() -> None:
    _disable()
    assert llm.is_enabled() is False
    assert llm.extract_cv("anything") is None
    assert llm.extract_job("anything") is None


def test_disabled_when_no_key() -> None:
    os.environ["USE_LLM_EXTRACTION"] = "true"
    os.environ.pop("OPENAI_API_KEY", None)
    assert llm.is_enabled() is False


def test_enabled_when_flag_and_key() -> None:
    _enable()
    try:
        assert llm.is_enabled() is True
    finally:
        _disable()


# ---------- happy paths ----------

_VALID_CV_JSON = """{
  "name": "Jane Doe",
  "summary": "Backend engineer",
  "skills": ["Python", "FastAPI"],
  "education": ["B.Sc. Computer Science"],
  "experience": ["Senior Engineer at Acme"],
  "projects": ["RAG demo"],
  "certifications": ["AWS SAA"],
  "languages": ["English"],
  "contact": {
    "email": "jane@example.com",
    "phone": "+1 415 555 0199",
    "linkedin": "https://linkedin.com/in/janedoe",
    "github": "https://github.com/janedoe",
    "website": "https://janedoe.dev"
  }
}"""


def test_extract_cv_happy_path() -> None:
    _enable()
    _patch_chat(_VALID_CV_JSON)
    try:
        parsed = llm.extract_cv("any cv text")
        assert parsed is not None
        assert parsed.name == "Jane Doe"
        assert "Python" in parsed.skills
        assert parsed.email == "jane@example.com"
        assert parsed.portfolio == "https://janedoe.dev"
    finally:
        _disable()


_VALID_JOB_JSON = """{
  "job_title": "Senior AI Engineer",
  "company": "Cortex",
  "location": "Berlin",
  "salary": "EUR 80K-110K",
  "employment_type": "full-time",
  "remote_type": "hybrid",
  "required_skills": ["Python", "FastAPI"],
  "preferred_skills": ["TypeScript"],
  "responsibilities": ["Build RAG pipelines"],
  "qualifications": ["5+ years Python"],
  "experience_level": "senior",
  "education_requirements": ["Bachelor in CS"],
  "technologies": ["Python", "FastAPI", "TypeScript"],
  "soft_skills": ["Mentoring"]
}"""


def test_extract_job_happy_path() -> None:
    _enable()
    _patch_chat(_VALID_JOB_JSON)
    try:
        parsed = llm.extract_job("any job text")
        assert parsed is not None
        assert parsed.job_title == "Senior AI Engineer"
        assert parsed.experience_level == "senior"
        assert "Python" in parsed.required_skills
        assert "Mentoring" in parsed.soft_skills
        # raw_text preserved (used downstream by scoring).
        assert parsed.raw_text == "any job text"
    finally:
        _disable()


def test_extract_job_strips_code_fences() -> None:
    _enable()
    _patch_chat("```json\n" + _VALID_JOB_JSON + "\n```")
    try:
        parsed = llm.extract_job("any")
        assert parsed is not None and parsed.job_title == "Senior AI Engineer"
    finally:
        _disable()


# ---------- failure modes return None ----------

def test_extract_returns_none_on_invalid_json() -> None:
    _enable()
    _patch_chat("this is not json")
    try:
        assert llm.extract_cv("x") is None
        assert llm.extract_job("x") is None
    finally:
        _disable()


def test_extract_returns_none_on_schema_violation() -> None:
    _enable()
    # `skills` must be a list; LLM returned a string.
    _patch_chat('{"name": "X", "skills": "Python"}')
    try:
        assert llm.extract_cv("x") is None
    finally:
        _disable()


def test_extract_returns_none_on_network_error() -> None:
    _enable()
    _patch_chat(RuntimeError("boom"))
    try:
        assert llm.extract_cv("x") is None
        assert llm.extract_job("x") is None
    finally:
        _disable()


# ---------- orchestrator falls back ----------

def test_orchestrator_uses_heuristic_when_disabled() -> None:
    _disable()
    cv = extraction.extract_cv(
        "Jane Doe\njane@example.com\n\nSkills\nPython, FastAPI\n"
    )
    assert cv.name == "Jane Doe"
    assert "Python" in cv.skills


def test_orchestrator_falls_back_when_llm_returns_none() -> None:
    _enable()
    _patch_chat("not json at all")
    try:
        cv = extraction.extract_cv(
            "Jane Doe\njane@example.com\n\nSkills\nPython, FastAPI\n"
        )
        # Heuristic kicked in; result is still useful.
        assert cv.name == "Jane Doe"
        assert "Python" in cv.skills
    finally:
        _disable()


def test_orchestrator_uses_llm_when_available() -> None:
    _enable()
    _patch_chat(_VALID_CV_JSON)
    try:
        cv = extraction.extract_cv("anything")
        assert cv.name == "Jane Doe"
        assert cv.email == "jane@example.com"
    finally:
        _disable()


def test_anthropic_provider_selected_when_only_anthropic_key_set() -> None:
    """No OPENAI_API_KEY but ANTHROPIC_API_KEY → provider auto-flips."""
    os.environ["USE_LLM_EXTRACTION"] = "true"
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
    try:
        assert llm.is_enabled() is True
        cfg = llm._config()
        assert cfg["provider"] == "anthropic"
        assert cfg["api_key"] == "test-anthropic-key"
        assert "anthropic.com" in cfg["base_url"]
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        _disable()


def test_anthropic_dispatch_builds_correct_payload() -> None:
    """The Anthropic helper splits system/messages, adds JSON-only
    instruction, prefills `{`, and hits the Messages API."""
    captured: dict = {}

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": '"ok": true}'}]}

    class _FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return _FakeResp()

    import httpx as _httpx
    original_client = _httpx.Client
    _httpx.Client = _FakeClient  # type: ignore[assignment]
    try:
        cfg = {
            "provider": "anthropic",
            "api_key": "test-anthropic-key",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-haiku-4-5",
            "timeout": 10.0,
        }
        reply = llm._chat_completion_anthropic(
            [
                {"role": "system", "content": "you are a parser"},
                {"role": "user", "content": "parse this"},
            ],
            cfg,
        )
    finally:
        _httpx.Client = original_client  # type: ignore[assignment]

    # URL is the Messages endpoint, not /chat/completions.
    assert captured["url"].endswith("/messages")
    # Anthropic-specific headers.
    assert captured["headers"]["x-api-key"] == "test-anthropic-key"
    assert captured["headers"]["anthropic-version"] == llm.ANTHROPIC_API_VERSION
    # System message lifted out; remaining messages have user + assistant
    # (the prefilled `{`).
    payload = captured["payload"]
    assert payload["system"] == "you are a parser"
    assert payload["messages"][-1] == {"role": "assistant", "content": "{"}
    assert "JSON only" in payload["messages"][-2]["content"]
    # The helper re-prepends `{` so callers see a complete object.
    assert reply.startswith("{") and reply.endswith("}")


def _run_all() -> None:
    tests = [
        test_disabled_when_flag_off,
        test_disabled_when_no_key,
        test_enabled_when_flag_and_key,
        test_extract_cv_happy_path,
        test_extract_job_happy_path,
        test_extract_job_strips_code_fences,
        test_extract_returns_none_on_invalid_json,
        test_extract_returns_none_on_schema_violation,
        test_extract_returns_none_on_network_error,
        test_orchestrator_uses_heuristic_when_disabled,
        test_orchestrator_falls_back_when_llm_returns_none,
        test_orchestrator_uses_llm_when_available,
        test_anthropic_provider_selected_when_only_anthropic_key_set,
        test_anthropic_dispatch_builds_correct_payload,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
