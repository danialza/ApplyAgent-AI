"""Single entry point for "rebuild master library from all sources".

Honours the hand-edit lock (`CVLibrary.manually_edited_at`): when
set, auto-rebuild paths (CV upload, document upload, URL add) skip
silently so the user's curation stays intact. Force-rebuild routes
bypass this.

Used by every place that previously inlined the same try/except
block: cv_routes upload + delete, source_routes URL CRUD, profile
build.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.db_models import CVLibrary

logger = logging.getLogger("ai_job_cv_matcher.master_rebuild")


def try_rebuild_master(db: Session, *, force: bool = False) -> bool:
    """Rebuild the master library from all sources.

    Returns True when a rebuild happened, False when skipped.
    `force=true` ignores the manual-edit lock AND clears it.
    Failures are logged but never raised so callers (upload endpoints)
    don't fail on a flaky LLM.
    """
    try:
        from app.services.cv_library_builder import build_library_from_all

        row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
        if row is not None and getattr(row, "manually_edited_at", None) and not force:
            logger.info(
                "Master rebuild skipped — library is hand-locked since %s.",
                row.manually_edited_at,
            )
            return False

        payload = build_library_from_all(db).model_dump()
        if row is None:
            row = CVLibrary(id=1)
            db.add(row)
        for k, v in payload.items():
            setattr(row, k, v)
        row.updated_at = datetime.utcnow()
        if force:
            row.manually_edited_at = None
            row.user_patches = []  # explicit reset

        # Replay user patches so Apply-Fix edits survive the rebuild.
        # Skipped on force=true (user explicitly chose to discard).
        if not force:
            from app.services.user_patches import replay_patches
            patches = list(getattr(row, "user_patches", None) or [])
            if patches:
                applied = replay_patches(row, patches)
                logger.info("Replayed %d/%d user patches after rebuild", applied, len(patches))

        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Master rebuild failed: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False
