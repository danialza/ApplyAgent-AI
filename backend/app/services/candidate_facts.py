"""Durable candidate-facts memory for the batch autopilot.

When the gap detector raises a clarifying question the master CV can't
answer (e.g. "How many years of TypeScript?", "Authorized to work in
the UK?"), the answer the user gives is stored here keyed by a stable
slug. Every later JD that raises the same key is answered from memory
instead of re-asking — so the system learns as it goes.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.db_models import CandidateFact


def normalise_key(key: str) -> str:
    """Stable slug for a question key. Lower snake-ish, no punctuation."""
    k = (key or "").strip().lower()
    k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    return k[:120]


def all_facts(db: Session) -> dict[str, str]:
    """Return {key: answer} for every stored fact."""
    return {
        f.key: f.answer
        for f in db.query(CandidateFact).all()
        if (f.answer or "").strip()
    }


def get_fact(db: Session, key: str) -> str | None:
    k = normalise_key(key)
    row = db.query(CandidateFact).filter(CandidateFact.key == k).first()
    return row.answer if row else None


def upsert_fact(db: Session, key: str, question: str, answer: str) -> CandidateFact:
    """Create or update a fact. Commits."""
    k = normalise_key(key)
    row = db.query(CandidateFact).filter(CandidateFact.key == k).first()
    if row is None:
        row = CandidateFact(key=k, question=(question or "").strip(), answer=(answer or "").strip())
        db.add(row)
    else:
        if question:
            row.question = question.strip()
        row.answer = (answer or "").strip()
    db.commit()
    db.refresh(row)
    return row


def delete_fact(db: Session, key: str) -> bool:
    k = normalise_key(key)
    row = db.query(CandidateFact).filter(CandidateFact.key == k).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
