"""Batch-autopilot worker.

Walks the queued BatchItems for a run, one at a time:
  fetch JD from the URL → parse company/role → dedupe → detect
  knowledge gaps (ask the user when needed) → render a tailored CV →
  save it to the Applications tracker → next.

Runs in a background thread with its own DB session. Items that hit a
knowledge gap are parked at `needs_input` (the worker does NOT block —
it moves on); once the user answers, the item is re-queued and picked
up on the next run.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.db_models import Application, BatchItem, CVLibrary

logger = logging.getLogger("ai_job_cv_matcher.batch_worker")

# One run at a time — protects the shared DB from two workers racing and
# keeps the subscription/API rate limits sane.
_run_lock = threading.Lock()


def _candidate_profile(db: Session) -> str:
    """Compact master-CV profile for the gap detector: summary + every
    skill + project/experience titles."""
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return ""
    parts: list[str] = []
    if getattr(row, "summary", ""):
        parts.append(str(row.summary))
    skills: list[str] = []
    for g in (row.skills_groups or []):
        items = g.get("items") if isinstance(g, dict) else getattr(g, "items", None)
        for s in (items or []):
            skills.append(str(s))
    if skills:
        parts.append("Skills: " + ", ".join(skills))
    titles: list[str] = []
    for sec in ("selected_projects", "additional_projects", "experience"):
        for e in (getattr(row, sec, None) or []):
            t = e.get("title") if isinstance(e, dict) else getattr(e, "title", "")
            if t:
                titles.append(str(t))
    if titles:
        parts.append("Work: " + "; ".join(titles))
    return "\n".join(parts)


def process_item(db: Session, item: BatchItem) -> None:
    """Advance a single item as far as it can go. Never raises."""
    from app.api.application_routes import hash_jd
    from app.models.schemas import RenderCVRequest
    from app.services import candidate_facts as facts
    from app.services.extraction import extract_job
    from app.services.gap_detector import detect_gaps
    from app.services.web_ingest import ingest

    try:
        # 1. Fetch the JD.
        item.status = "fetching"
        item.error = ""
        db.commit()
        _kind, raw, _ext, err = ingest(item.url)
        jd_text = (raw or "").strip()
        if not jd_text:
            item.status = "failed"
            item.error = err or "No job description text found at this URL."
            db.commit()
            return
        item.jd_text = jd_text

        # 2. Parse company / role.
        try:
            parsed = extract_job(jd_text)
            item.company = (parsed.company or "").strip()
            item.role = (parsed.job_title or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("batch: extract_job failed for %s: %s", item.url, exc)
        db.commit()

        # 3. Dedupe against the tracker.
        h = hash_jd(jd_text)
        dup = db.query(Application).filter(Application.jd_hash == h).first() if h else None
        if dup is not None:
            item.status = "duplicate"
            item.application_id = dup.id
            db.commit()
            return

        # 4. Knowledge gaps — ONCE per item. A resumed/retried item has
        # gaps_checked set and skips straight to rendering, so the LLM
        # can't re-ask the same gap under a different key.
        if not item.gaps_checked:
            known = facts.all_facts(db)
            gaps = detect_gaps(
                jd_text=jd_text,
                candidate_profile=_candidate_profile(db),
                known_facts=known,
            )
            item.gaps_checked = True
            if gaps:
                item.pending_questions = gaps
                item.status = "needs_input"
                db.commit()
                return
            item.pending_questions = []
            db.commit()

        # 5. Render the tailored CV via the real render pipeline.
        item.status = "rendering"
        db.commit()
        from app.api.cv_render_routes import render_tailored_cv
        resp = render_tailored_cv(
            RenderCVRequest(
                job_text=jd_text,
                compile_pdf=True,
                use_llm=True,
                target_length="auto",
                target_keyword_coverage=0.95,
            ),
            db,
        )

        # 6. Save to the Applications tracker.
        app_row = Application(
            company=item.company or (resp.job_company or ""),
            role=item.role or (resp.job_title or ""),
            status="To-Apply",
            url=item.url,
            jd_text=jd_text,
            jd_hash=h,
            cv_latex=resp.latex or "",
            cv_pdf_b64=resp.pdf_b64 or "",
            cv_filename=resp.suggested_filename or "tailored-cv",
            keyword_coverage=resp.keyword_coverage,
        )
        db.add(app_row)
        db.commit()
        db.refresh(app_row)

        item.application_id = app_row.id
        item.keyword_coverage = resp.keyword_coverage
        item.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("batch: item %s failed", item.id)
        try:
            item.status = "failed"
            item.error = str(exc)[:500]
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()


def run_batch(batch_id: str) -> None:
    """Process every queued item in a batch, sequentially. Intended to
    run in a background thread. Re-entrant-safe via a global lock."""
    with _run_lock:
        db = SessionLocal()
        try:
            while True:
                item = (
                    db.query(BatchItem)
                    .filter(BatchItem.batch_id == batch_id, BatchItem.status == "queued")
                    .order_by(BatchItem.id)
                    .first()
                )
                if item is None:
                    break
                process_item(db, item)
        finally:
            db.close()


def start_batch_async(batch_id: str) -> None:
    """Fire-and-forget a run_batch in a daemon thread."""
    t = threading.Thread(target=run_batch, args=(batch_id,), daemon=True)
    t.start()
