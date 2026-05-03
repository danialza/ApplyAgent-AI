"""Extraction orchestrator — LLM first (when enabled), heuristic fallback.

All call sites that previously called `parse_cv_text` / `parse_job_text`
should call `extract_cv` / `extract_job` from this module instead. The
behaviour is identical when LLM extraction is disabled.

Logs which path was used at INFO level so it's easy to confirm the LLM is
actually being hit during a run.
"""
from __future__ import annotations

import logging

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


def extract_job(text: str) -> ParsedJob:
    """LLM-first JD extraction with rule-based fallback. Never returns None."""
    if llm.is_enabled():
        result = llm.extract_job(text)
        if result is not None:
            logger.info("JD extraction: LLM (model=%s)", llm._config()["model"])
            return result
        logger.info("JD extraction: LLM failed → falling back to rule-based parser.")
    else:
        logger.debug("JD extraction: LLM disabled → using rule-based parser.")
    return parse_job_text(text)
