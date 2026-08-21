"""Extraction orchestrator — LLM first (when enabled), heuristic fallback.

All call sites that previously called `parse_cv_text` / `parse_job_text`
should call `extract_cv` / `extract_job` from this module instead. The
behaviour is identical when LLM extraction is disabled.

Logs which path was used at INFO level so it's easy to confirm the LLM is
actually being hit during a run.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import threading
from collections import OrderedDict

from app.services import llm_extraction_service as llm
from app.services.cv_parser import ParsedCV, parse_cv_text
from app.services.job_parser import ParsedJob, parse_job_text

logger = logging.getLogger("ai_job_cv_matcher.extraction")


def extract_cv(text: str) -> ParsedCV:
    """LLM-first CV extraction with rule-based fallback. Never returns None."""
    if llm.is_enabled():
        result = llm.extract_cv(text)
        if result is not None:
            logger.info("CV extraction: LLM (model=%s)", llm._config()["model"])
            return result
        logger.info("CV extraction: LLM failed → falling back to rule-based parser.")
    else:
        logger.debug("CV extraction: LLM disabled → using rule-based parser.")
    return parse_cv_text(text)


# One JD, one parse. The LLM returns a different skill list every time it
# sees the same posting -- two parses of one Pay.UK ad agreed on only 6 of
# 13 skills. That drift is invisible until two stages parse the same text:
# the pre-flight drafts evidence for the skills IT saw, the render then
# grades against the skills IT saw, and bullets get written for skills the
# scorecard never checks while the ones it does check stay red. Caching by
# content hash makes every stage of one job agree, and saves a call.
_JOB_CACHE: OrderedDict[str, ParsedJob] = OrderedDict()
_JOB_CACHE_MAX = 32
_JOB_CACHE_LOCK = threading.Lock()


def _job_cache_key(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def clear_job_cache() -> None:
    """Drop every cached parse. For tests and for a forced re-parse."""
    with _JOB_CACHE_LOCK:
        _JOB_CACHE.clear()


def extract_job(text: str) -> ParsedJob:
    """LLM-first JD extraction with rule-based fallback. Never returns None.

    Repeat calls for the same posting return the same parse, so every
    stage of a render agrees on what the job asked for.
    """
    key = _job_cache_key(text or "")
    with _JOB_CACHE_LOCK:
        hit = _JOB_CACHE.get(key)
        if hit is not None:
            _JOB_CACHE.move_to_end(key)
    if hit is not None:
        logger.info("JD extraction: cache hit")
        return copy.deepcopy(hit)

    parsed = _extract_job_uncached(text)
    with _JOB_CACHE_LOCK:
        _JOB_CACHE[key] = copy.deepcopy(parsed)
        while len(_JOB_CACHE) > _JOB_CACHE_MAX:
            _JOB_CACHE.popitem(last=False)
    return parsed


def _extract_job_uncached(text: str) -> ParsedJob:
    if llm.is_enabled():
        result = llm.extract_job(text)
        if result is not None:
            logger.info("JD extraction: LLM (model=%s)", llm._config()["model"])
            return result
        logger.info("JD extraction: LLM failed → falling back to rule-based parser.")
    else:
        logger.debug("JD extraction: LLM disabled → using rule-based parser.")
    return parse_job_text(text)
