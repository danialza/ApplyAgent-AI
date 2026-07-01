"""Strip LLM meta-commentary that must never reach the rendered CV.

The polish / coverage-boost models are told to rewrite bullets "to match
the JD". Occasionally a model breaks character and bleeds its own
*rationale* into the bullet text, e.g.:

    "...collaboration with product managers and domain experts the JD requires."
    "...mirroring the JD's RAG pipeline and backend API requirements."

Those clauses explain WHY a bullet was written — they are not the
candidate's experience and must never appear on the CV. This module
detects such phrasing and either trims the offending trailing clause or
rejects the rewrite so the caller falls back to the original library
text.

Design goals:
  * Zero false positives on legitimate CV content ("regulatory
    requirements", "compliance requirements", "met SLA requirements"
    are all fine — only JD/role-referential meta is caught).
  * Deterministic, dependency-free, fast (pure regex).
"""
from __future__ import annotations

import re

# Phrases that are almost never legitimate CV prose — they reference the
# job posting itself or explain the bullet's purpose. Matching ANY of
# these flags the text as meta-contaminated.
_META_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bthe\s+JD\b",
        r"\bJD['’]s\b",
        r"\bthe\s+job\s+(description|posting|ad|advert|spec|listing)\b",
        r"\bmirror(?:s|ing|ed)?\s+the\s+(?:JD|job|role|position|posting)\b",
        r"\b(?:align(?:s|ing|ed)?|match(?:es|ing|ed)?|tailored|aligned)\s+(?:to|with)\s+the\s+(?:JD|job|role|position|posting)\b",
        r"\bas\s+(?:the\s+)?(?:JD|job|role|position)\s+requir",
        r"\bas\s+required\s+by\s+the\s+(?:JD|job|role|position)\b",
        r"\bwhat\s+the\s+(?:JD|job|role|position)\s+(?:requires|wants|needs|asks)\b",
        r"\bthe\s+(?:role|position|job)\s+requires\b",
        r"\b(?:requirements?|skills?)\s+(?:the|that the)\s+(?:JD|job|role|position)\b",
        r"\b(?:to|that)\s+match(?:es)?\s+the\s+(?:JD|job|role|position)\b",
        r"\breflect(?:s|ing|ed)?\s+the\s+(?:JD|job|role|position)['’]?s?\s+(?:require|need|ask|want|priorit)",
        r"\bdemonstrat(?:es|ing)\b[^.]*\b(?:JD|job\s+description)\b",
        # Rationale tails that editorialize about fit without naming the
        # JD outright, e.g. "— directly mirroring the architecture needed
        # for regulatory document automation", "reflecting the approach
        # required for ...". Anchored to a mirror/reflect/match verb +
        # a "needed/required/expected" so legitimate prose isn't caught.
        r"\b(?:mirror(?:s|ing|ed)?|reflect(?:s|ing|ed)?|echo(?:es|ing|ed)?)\s+the\s+[\w\s,\-]{0,60}?\b(?:needed|required|expected|demanded|called\s+for)\b",
        r"\b(?:directly\s+)?(?:mirror(?:s|ing|ed)?|reflect(?:s|ing|ed)?)\s+(?:the\s+)?(?:architecture|approach|design|pattern|stack|skills?|requirements?)\s+(?:needed|required|expected|for\b)",
    )
]

# Clause separators we can safely cut a trailing meta-clause at. Ordered
# longest-first so multi-word separators win.
_CUTPOINTS = [
    " — ", " – ", " -- ",
    ", mirroring", ", aligning", ", matching", ", reflecting",
    ", demonstrating", ", as required", ", as the", ", which the",
    ", to match", ", tailored",
    " mirroring ", " reflecting the ", " aligning with the ",
    " which the ", " that the ",
    ", ",
    " - ",
]

_MIN_KEEP = 25  # a cleaned bullet shorter than this is too gutted; reject


def has_meta(text: str) -> bool:
    """True when `text` contains JD/role-referential meta-commentary."""
    if not text:
        return False
    return any(p.search(text) for p in _META_PATTERNS)


def _earliest_meta_pos(text: str) -> int:
    pos = len(text) + 1
    for p in _META_PATTERNS:
        m = p.search(text)
        if m and m.start() < pos:
            pos = m.start()
    return pos if pos <= len(text) else -1


def strip_meta(text: str) -> str:
    """Remove a trailing meta clause from `text`.

    Cuts at the last safe clause separator BEFORE the meta phrase. Returns
    the trimmed text (with a single trailing period) or "" when nothing
    salvageable remains.
    """
    if not text or not has_meta(text):
        return text
    mpos = _earliest_meta_pos(text)
    if mpos < 0:
        return text
    # Find the latest cutpoint that sits at/just before the meta phrase.
    best = -1
    for sep in _CUTPOINTS:
        idx = text.rfind(sep, 0, mpos + 1)
        if idx > best:
            best = idx
    head = text[:best] if best > 0 else ""
    head = head.strip().rstrip(",;:—–-").strip()
    if not head:
        return ""
    # If the head STILL contains meta (multiple leaks), drop it.
    if has_meta(head):
        return ""
    if not head.endswith((".", "!", "?")):
        head += "."
    return head


def clean_bullet(original: str, rewritten: str) -> str:
    """Return a meta-free bullet.

    Prefers a trimmed version of `rewritten`; if that can't be salvaged
    (too short, or meta survives), falls back to `original`. `original`
    is itself scrubbed as a last resort so a contaminated library bullet
    can't slip through either.
    """
    rw = (rewritten or "").strip()
    if rw and not has_meta(rw):
        return rw
    if rw:
        cleaned = strip_meta(rw)
        if cleaned and len(cleaned) >= _MIN_KEEP and not has_meta(cleaned):
            return cleaned
    # Fall back to the original, scrubbing it too just in case.
    orig = (original or "").strip()
    if orig and has_meta(orig):
        scrubbed = strip_meta(orig)
        return scrubbed if scrubbed else ""
    return orig


def clean_text_block(text: str) -> str:
    """Scrub a prose block (e.g. the summary). Drops whole sentences that
    carry meta-commentary; keeps the rest. Returns "" only if everything
    was meta."""
    if not text or not has_meta(text):
        return text
    # Split into sentences, keep non-meta ones.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = []
    for s in sentences:
        if not s.strip():
            continue
        if has_meta(s):
            trimmed = strip_meta(s)
            if trimmed and not has_meta(trimmed):
                kept.append(trimmed)
            # else drop the sentence entirely
        else:
            kept.append(s.strip())
    return " ".join(kept).strip()
