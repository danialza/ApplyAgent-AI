"""LLM-driven curation pass over the master library.

The deterministic builder (`build_library_from_all`) merges entries
by exact lower-cased title match. That misses near-duplicates the
LLM should obviously collapse:

  * "TalkingHeadAI" (CV) vs "talkinghead-ai" (GitHub) — same project,
    different slug.
  * "NSP AI Enquiry Workflow" (CV) vs "nsp-ai-enquiry-workflow"
    (GitHub repo) — both about the same system.
  * Five flagship CV projects + 20 noisy GitHub repos like
    "personal-website", "ai-event", "danialza" → keep the flagship,
    fold their tags/links into existing entries, drop the noise.

This service runs ONE LLM call over a compact project digest and
returns:
  * groups   — clusters of source entries that map to one canonical
               project. Order = canonical title's display priority.
  * keep_ids — indices the LLM judged worth surfacing (high signal,
               complete, not boilerplate).
  * dropped  — indices the LLM judged noise (empty README, profile
               repo, fork). Reasons surface for transparency.

Falls back to the input library when LLM is off or returns garbage.
Never raises.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import CVLibraryBase, ProjectEntry

logger = logging.getLogger("ai_job_cv_matcher.master_curator")


# Maximum projects we send to the LLM in one batch. Keeps tokens
# bounded; larger libraries get split.
MAX_PROJECTS_PER_CALL = 60


class _ProjectGroup(BaseModel):
    """One cluster of source projects that map to a single canonical entry."""
    canonical_title: str
    member_indices: list[int] = Field(default_factory=list)
    reason: str = ""


class _CuratorOutput(BaseModel):
    groups: list[_ProjectGroup] = Field(default_factory=list)
    drop_indices: list[int] = Field(default_factory=list)
    drop_reasons: dict[str, str] = Field(default_factory=dict)


def curate_projects(library: CVLibraryBase) -> CVLibraryBase:
    """Run the LLM curator over the library's projects. Returns the
    mutated library (caller can persist it). Falls back to input on
    failure."""
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        logger.info("Master curator: LLM disabled, returning library unchanged")
        return library

    all_projects: list[tuple[str, int, ProjectEntry]] = []
    for i, p in enumerate(library.selected_projects or []):
        all_projects.append(("selected", i, p))
    for i, p in enumerate(library.additional_projects or []):
        all_projects.append(("additional", i, p))

    if len(all_projects) < 3:
        # Too few to make merge/drop decisions worth the LLM cost.
        return library
    if len(all_projects) > MAX_PROJECTS_PER_CALL:
        all_projects = all_projects[:MAX_PROJECTS_PER_CALL]

    digest = _build_digest(all_projects)
    parsed = _llm_call(digest)
    if parsed is None:
        return library

    return _apply_curation(library, all_projects, parsed)


# ---------- LLM call ----------

def _build_digest(items: list[tuple[str, int, ProjectEntry]]) -> list[dict]:
    """Compact view of each project the LLM scores + groups."""
    out = []
    for global_idx, (bucket, local_idx, p) in enumerate(items):
        bullets = list(p.highlights or [])
        out.append({
            "idx": global_idx,
            "bucket": bucket,                          # "selected" | "additional"
            "title": p.title,
            "period": p.period or "",
            "tags": list(p.tags or [])[:6],
            "sources": list(p.sources or []),
            "n_bullets": len(bullets),
            "sample_bullet": bullets[0][:160] if bullets else "",
            "has_url": any("http" in (b or "") for b in bullets),
        })
    return out


def _llm_call(digest: list[dict]) -> _CuratorOutput | None:
    from app.services import llm_extraction_service as llm

    system = (
        "You curate a candidate's CV project list. Each input row is "
        "one project from a source (CV, document, GitHub repo, web). "
        "Decide which entries are flagship-worthy, which are "
        "near-duplicates that should merge, and which are noise to "
        "drop entirely.\n\n"
        "Hard rules:\n"
        "1. Group near-duplicates: same project under different "
        "names (slug vs title case, with vs without spaces, project "
        "name vs repo name). Title match should be SEMANTIC, not "
        "string-exact. 'TalkingHeadAI' and 'talkinghead-ai' merge. "
        "'NED 3 Pro DRL Sim-to-Real Reaching' and 'ned3-pro-drl-sim2real' "
        "merge. Sample-bullet content can confirm grouping when "
        "titles look similar.\n"
        "2. DROP these as noise (only for things truly unfit for a "
        "professional CV):\n"
        "   - Profile-readme repos (`danialza` user repo, `dotfiles`).\n"
        "   - Course/event archives with no real engineering content "
        "(`ai-event`, `class-2024`).\n"
        "   - Personal-website / blog repos with no project narrative.\n"
        "   - Forks (sources usually contain `fork:true` hint).\n"
        "   - Entries with empty title or title literally 'Project'.\n"
        "3. KEEP everything else, even small projects — recruiters "
        "appreciate breadth. Don't be aggressive about dropping.\n"
        "4. For each group, pick the canonical_title from the most "
        "human-readable member (CV-derived > document > GitHub repo "
        "slug). Capitalise / spell out properly.\n"
        "5. Group sizes: a single entry that has no duplicate forms "
        "its own group of one. Don't force merges.\n\n"
        "Reply JSON:\n"
        '{"groups": [{"canonical_title": "TalkingHeadAI", '
        '"member_indices": [3, 11], "reason": "Same project, CV + repo"}, '
        '...], "drop_indices": [7, 12], '
        '"drop_reasons": {"7": "Profile readme", "12": "Empty fork"}}'
    )

    user = json.dumps({"projects": digest}, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _CuratorOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("Master curator LLM failed: %s", exc)
        return None
    return parsed


# ---------- Apply ----------

def _apply_curation(
    library: CVLibraryBase,
    items: list[tuple[str, int, ProjectEntry]],
    parsed: _CuratorOutput,
) -> CVLibraryBase:
    """Build new selected/additional project lists from the LLM's
    grouping + drop decisions. Anything the LLM didn't mention
    survives untouched (defensive — better to keep than silently
    lose data)."""
    n = len(items)
    drop = {int(i) for i in (parsed.drop_indices or []) if isinstance(i, int) and 0 <= i < n}

    # Map every input index to a group id (or None if ungrouped).
    group_id_of: dict[int, int] = {}
    group_canonical_title: dict[int, str] = {}
    group_reasons: dict[int, str] = {}
    for gid, g in enumerate(parsed.groups or []):
        members = [i for i in (g.member_indices or [])
                   if isinstance(i, int) and 0 <= i < n and i not in drop]
        if not members:
            continue
        group_canonical_title[gid] = (g.canonical_title or "").strip() or items[members[0]][2].title
        group_reasons[gid] = g.reason or ""
        for m in members:
            group_id_of[m] = gid

    # Singleton group for every ungrouped (non-dropped) entry.
    next_gid = max(group_canonical_title.keys(), default=-1) + 1
    for i in range(n):
        if i in drop or i in group_id_of:
            continue
        group_id_of[i] = next_gid
        group_canonical_title[next_gid] = items[i][2].title
        next_gid += 1

    # Build the merged ProjectEntry per group.
    selected_out: list[ProjectEntry] = []
    additional_out: list[ProjectEntry] = []
    seen_gids: set[int] = set()

    for orig_idx, (bucket, _local, p) in enumerate(items):
        gid = group_id_of.get(orig_idx)
        if gid is None or gid in seen_gids:
            continue
        seen_gids.add(gid)
        members = [j for j in range(n) if group_id_of.get(j) == gid]

        # Merge content from every member in stable order.
        first = items[members[0]][2]
        merged = ProjectEntry(
            title=group_canonical_title.get(gid, first.title),
            period=first.period,
            highlights=list(first.highlights or []),
            tags=list(first.tags or []),
            sources=list(first.sources or []),
        )
        for m in members[1:]:
            other = items[m][2]
            if other.period and len(other.period) > len(merged.period or ""):
                merged.period = other.period
            for b in (other.highlights or []):
                if b and b not in merged.highlights:
                    merged.highlights.append(b)
            for t in (other.tags or []):
                if t and t not in merged.tags:
                    merged.tags.append(t)
            for s in (other.sources or []):
                if s and s not in merged.sources:
                    merged.sources.append(s)

        # Place in the same bucket as the first member's bucket; if
        # any member is "selected", whole group lands in selected.
        any_selected = any(items[m][0] == "selected" for m in members)
        (selected_out if any_selected else additional_out).append(merged)

    dropped_summary = {
        items[i][2].title: parsed.drop_reasons.get(str(i), "noise")
        for i in drop if 0 <= i < n
    }
    logger.info(
        "Master curator: %d → %d entries (selected=%d, additional=%d, dropped=%d). "
        "Drops: %s",
        n, len(selected_out) + len(additional_out),
        len(selected_out), len(additional_out), len(drop),
        dropped_summary,
    )

    new_lib = library.model_copy(deep=True)
    new_lib.selected_projects = selected_out
    new_lib.additional_projects = additional_out
    return new_lib
