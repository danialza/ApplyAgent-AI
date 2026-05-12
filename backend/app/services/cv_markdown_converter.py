"""Convert raw CV text → strict cv.md via the configured LLM.

Reuses the standalone prompt at ``docs/cv_to_markdown_prompt.md`` so the
in-app converter and the copy-paste-to-Claude flow stay in sync. Edit
the doc, both pick it up.

Pipeline:
  1. Load the SYSTEM block from docs/cv_to_markdown_prompt.md.
  2. Send {system, user=<cv_text>} to the LLM as plain text (no JSON).
  3. Strip accidental ```markdown fences the model might add.
  4. Return the markdown string.

Errors propagate as RuntimeError with a human-readable message.
"""
from __future__ import annotations

from pathlib import Path

from app.services import llm_extraction_service as llm


def _load_system_prompt() -> str:
    """Extract the SYSTEM section from docs/cv_to_markdown_prompt.md.

    The doc structure is::

        ## SYSTEM (instructions to the model)
        ...rules...
        ---
        ## INPUT — paste your existing CV below
        ...

    We slice between those two h2 markers so changes to the doc flow
    through without code edits.
    """
    this = Path(__file__).resolve()
    candidates: list[Path] = [Path("/app/docs/cv_to_markdown_prompt.md")]
    for n in range(2, 6):
        if len(this.parents) > n:
            candidates.append(this.parents[n] / "docs" / "cv_to_markdown_prompt.md")
    for p in candidates:
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            break
    else:
        raise RuntimeError(
            "docs/cv_to_markdown_prompt.md missing from the running image."
        )

    start = text.find("## SYSTEM")
    end = text.find("## INPUT")
    if start == -1 or end == -1 or end <= start:
        # Fall back to the full doc — still works, just longer prompt.
        return text
    return text[start:end].strip()


def _strip_fences(md: str) -> str:
    """Drop a leading ```markdown / ``` fence and trailing ``` if the
    model wrapped the whole reply in a code block (rule #1 in the
    prompt forbids this, but small models sometimes do it anyway)."""
    s = (md or "").strip()
    if s.startswith("```"):
        # Drop the first line (``` or ```markdown).
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[: -3].rstrip()
    return s.strip()


def convert_cv_text_to_markdown(cv_text: str) -> str:
    """Run the LLM and return one cv.md-shaped string.

    Raises RuntimeError if the LLM layer is disabled, unreachable, or
    returns something that clearly isn't a CV markdown file.
    """
    text = (cv_text or "").strip()
    if not text:
        raise RuntimeError("Empty CV text — nothing to convert.")

    if not llm.is_enabled():
        raise RuntimeError(
            "LLM disabled. Set USE_LLM_EXTRACTION=true and an API key "
            "(OPENAI_API_KEY or ANTHROPIC_API_KEY) in .env, then restart."
        )

    system = _load_system_prompt()
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "<<< CV TEXT\n"
                + text
                + "\nCV TEXT >>>\n\n"
                "Emit the cv.md now. First line must be `# ` plus the "
                "candidate's full name. No preamble, no code fences."
            ),
        },
    ]

    reply = llm.chat_text(messages)
    md = _strip_fences(reply)

    # Sanity check — first non-empty line must be an h1 title.
    first = next((ln for ln in md.splitlines() if ln.strip()), "")
    if not first.startswith("# "):
        raise RuntimeError(
            "LLM did not return a cv.md (no `# Name` header on line 1). "
            "Try again, or paste a longer source CV."
        )
    return md
