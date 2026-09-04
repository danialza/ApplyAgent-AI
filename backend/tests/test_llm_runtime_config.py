from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.cv_render_routes import set_llm_config


def test_sets_provider_and_model_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_LLM_EXTRACTION", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = set_llm_config({"provider": "anthropic", "model": "claude-sonnet-5"})

    assert result == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "enabled": True,
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"provider": "unknown", "model": "x"}, "provider must be one of"),
        ({"provider": "anthropic", "model": ""}, "model required"),
    ],
)
def test_rejects_invalid_runtime_config(payload: dict, message: str) -> None:
    with pytest.raises(HTTPException) as raised:
        set_llm_config(payload)

    assert raised.value.status_code == 400
    assert message in str(raised.value.detail)
