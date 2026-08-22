"""Focused checks for the LLM CV-polish contract.

Run without pytest:
    python -m tests.test_codex_cv_polish
"""
from __future__ import annotations

import json

from app.models.schemas import RenderCVRequest
from app.services import llm_extraction_service as llm
from app.services.codex_cv_polish import (
    _coerce_json,
    _prepare_summary,
    polish_library_with_llm,
)
from app.services.cv_coverage_booster import _bullet_snapshot
from tests.test_cv_renderer import _sample_library


def test_coerce_json_accepts_provider_wrapper_text() -> None:
    payload = _coerce_json('Here is the result:\n{"summary": "ok"}\nusage: 12')
    assert payload == {"summary": "ok"}


def test_api_defaults_to_llm_aggressive_tailoring() -> None:
    request = RenderCVRequest()
    assert request.use_llm is True
    assert request.enhance_tailor is True


def test_summary_removes_transition_language_and_caps_sentences() -> None:
    summary = (
        "Systems Lead with 7+ years, bridging into Site Reliability Engineering "
        "through practical work in CI/CD and observability. "
        "Built Python automation and containerized services. "
        "Reduced repeat operational work through reusable workflows. "
        "This fourth sentence should not survive."
    )
    prepared = _prepare_summary(summary)
    assert "bridging into" not in prepared.lower()
    assert "applying Site Reliability Engineering practices" in prepared
    assert "fourth sentence" not in prepared


def test_coverage_snapshot_is_limited_to_rendered_entries() -> None:
    snapshot = _bullet_snapshot(_sample_library(), ["Senior Systems Developer"])
    assert snapshot
    assert {item["title"] for item in snapshot} == {
        "Senior Systems Developer @ Green Wing Co.",
    }


def test_polish_focuses_prompt_and_preserves_identity_titles() -> None:
    library = _sample_library()
    from app.models.schemas import JobParsed
    parsed_job = JobParsed(
        job_title="Principal Site Reliability Engineer",
        required_skills=["Site Reliability Engineering", "Terraform"],
        raw_text="Principal SRE using Terraform and automation.",
    )

    captured: list[dict[str, str]] = []
    response = {
        "summary": "Systems and Technical Lead with 7+ years building reliable automation.",
        "bold_keywords": ["Site Reliability Engineering", "Terraform"],
        # These renamed identity fields must not match or mutate the library.
        "selected_projects": [
            {"title": "Renamed Project", "highlights": ["Invented rewrite."]},
        ],
        "additional_projects": [],
        "experience": [
            {
                "title": "Principal Site Reliability Engineer",
                "company": "Green Wing Co.",
                "highlights": ["Invented role rewrite."],
            },
        ],
        "extra_skills": [],
    }

    old_enabled = llm.is_enabled
    old_chat = llm._chat_completion  # type: ignore[attr-defined]
    llm.is_enabled = lambda: True  # type: ignore[assignment]

    def _fake_chat(messages, **_kwargs):
        captured.extend(messages)
        return json.dumps(response)

    llm._chat_completion = _fake_chat  # type: ignore[attr-defined]
    try:
        polished, _bold, error = polish_library_with_llm(
            library,
            parsed_job,
            enhance=True,
            focus_sections={
                "projects": ["AI Job-CV Matching Agent"],
                "experience": ["Senior Systems Developer"],
            },
        )
    finally:
        llm.is_enabled = old_enabled  # type: ignore[assignment]
        llm._chat_completion = old_chat  # type: ignore[attr-defined]

    assert not error and polished is not None
    prompt = captured[-1]["content"]
    assert "AI Job-CV Matching Agent" in prompt
    assert "WordPress Plugin Builder" not in prompt
    assert "Senior Systems Developer" in prompt

    assert [p.title for p in polished.selected_projects] == [
        "AI Job-CV Matching Agent", "WordPress Plugin Builder",
    ]
    assert polished.experience[0].title == "Senior Systems Developer"
    assert polished.experience[0].highlights == ["Worked on production platforms."]


def _run_all() -> None:
    tests = [
        test_coerce_json_accepts_provider_wrapper_text,
        test_api_defaults_to_llm_aggressive_tailoring,
        test_summary_removes_transition_language_and_caps_sentences,
        test_coverage_snapshot_is_limited_to_rendered_entries,
        test_polish_focuses_prompt_and_preserves_identity_titles,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
