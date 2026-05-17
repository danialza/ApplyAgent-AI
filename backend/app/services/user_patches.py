"""Library-mutating patches the user accumulates via Apply Fix.

Each patch is a dict ``{"kind": str, "payload": dict}`` matching one
of the FixAction kinds the audit emits. The same code path:
  * applies a patch live (apply-fix endpoint)
  * replays the full patch list after every source-driven rebuild
    so user edits always survive (master_rebuild.try_rebuild_master).

Failed patches (target gone after a source delete, bad payload) are
logged and skipped — never raised — so a stale patch can't break
the rebuild.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai_job_cv_matcher.user_patches")

SECTIONS = {
    "selected_projects",
    "additional_projects",
    "experience",
    "education",
    "certifications",
    "publications",
}


def validate_action(kind: str, payload: dict) -> None:
    """Raise ValueError on obvious shape mistakes. Doesn't touch DB."""
    if kind == "drop_entry":
        if payload.get("section") not in SECTIONS:
            raise ValueError(f"bad section: {payload.get('section')!r}")
        if not isinstance(payload.get("index"), int):
            raise ValueError("index must be int")
    elif kind == "set_field":
        if payload.get("section") not in SECTIONS:
            raise ValueError(f"bad section: {payload.get('section')!r}")
        if not isinstance(payload.get("index"), int):
            raise ValueError("index must be int")
        if not (payload.get("field") or "").strip():
            raise ValueError("field required")
    elif kind == "split_education":
        if not isinstance(payload.get("index"), int):
            raise ValueError("index must be int")
    elif kind == "truncate_field":
        if payload.get("section") not in SECTIONS:
            raise ValueError(f"bad section: {payload.get('section')!r}")
        if not isinstance(payload.get("index"), int):
            raise ValueError("index must be int")
        if not (payload.get("field") or "").strip():
            raise ValueError("field required")
    elif kind == "set_summary":
        if not isinstance(payload.get("value", ""), str):
            raise ValueError("value must be str")
    elif kind == "set_header_field":
        if not (payload.get("field") or "").strip():
            raise ValueError("field required")
    else:
        raise ValueError(f"unknown kind: {kind!r}")


def apply_action(row, kind: str, payload: dict) -> None:
    """Mutate `row` (CVLibrary ORM instance) in place."""
    if kind == "drop_entry":
        section = payload["section"]
        idx = payload["index"]
        lst = list(getattr(row, section, None) or [])
        if 0 <= idx < len(lst):
            lst.pop(idx)
        setattr(row, section, lst)
    elif kind == "set_field":
        section = payload["section"]
        idx = payload["index"]
        field = payload["field"]
        value = payload.get("value", "")
        lst = list(getattr(row, section, None) or [])
        if 0 <= idx < len(lst):
            target = dict(lst[idx]) if not isinstance(lst[idx], dict) else lst[idx]
            target[field] = value
            lst[idx] = target
            setattr(row, section, lst)
    elif kind == "split_education":
        idx = payload["index"]
        edu = list(getattr(row, "education", None) or [])
        if 0 <= idx < len(edu):
            entry = dict(edu[idx]) if not isinstance(edu[idx], dict) else edu[idx]
            entry["institution"] = (payload.get("new_institution") or "").strip()
            entry["degree"] = (payload.get("new_degree") or "").strip()
            edu[idx] = entry
            row.education = edu
    elif kind == "truncate_field":
        section = payload["section"]
        idx = payload["index"]
        field = payload["field"]
        max_chars = int(payload.get("max_chars", 180))
        lst = list(getattr(row, section, None) or [])
        if 0 <= idx < len(lst):
            target = dict(lst[idx]) if not isinstance(lst[idx], dict) else lst[idx]
            cur = (target.get(field) or "")[:max_chars]
            target[field] = cur
            lst[idx] = target
            setattr(row, section, lst)
    elif kind == "set_summary":
        row.summary = (payload.get("value") or "").strip()
    elif kind == "set_header_field":
        field = payload["field"]
        value = payload.get("value", "")
        header = dict(getattr(row, "header", None) or {})
        header[field] = value
        row.header = header
    else:
        raise ValueError(f"unknown kind: {kind!r}")


def replay_patches(row, patches: list[dict]) -> int:
    """Replay every patch on `row`. Returns count of successful applies.
    Never raises — bad patches log + skip so a single stale entry
    can't break the whole rebuild."""
    applied = 0
    for i, patch in enumerate(patches or []):
        if not isinstance(patch, dict):
            continue
        kind = patch.get("kind", "")
        payload = patch.get("payload", {}) or {}
        try:
            validate_action(kind, payload)
            apply_action(row, kind, payload)
            applied += 1
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            logger.info("Skipping stale user patch %d (%s): %s", i, kind, exc)
    return applied
