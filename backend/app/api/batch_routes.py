"""Batch-autopilot API.

Paste a list of job URLs → the worker fetches each JD, tailors a CV,
and files it in the Applications tracker, asking the user only when it
hits a knowledge gap it can't resolve from the master CV or its
learned facts.

    POST /api/batch                  start a run from a list of URLs
    GET  /api/batch/latest           items of the most recent run (poll)
    GET  /api/batch/{batch_id}       items of a specific run
    POST /api/batch/answer           answer pending questions (→ memory)
    POST /api/batch/item/{id}/retry  re-queue a failed / parked item
    GET  /api/facts                  list learned candidate facts
    POST /api/facts                  upsert a fact
    DELETE /api/facts/{key}          forget a fact
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import BatchItem, CandidateFact
from app.services import candidate_facts as facts_svc
from app.services.batch_worker import start_batch_async

router = APIRouter(prefix="/api", tags=["batch"])


# ---------- schemas ----------

class BatchCreate(BaseModel):
    urls: list[str] = Field(default_factory=list)


class BatchItemOut(BaseModel):
    id: int
    batch_id: str
    url: str
    status: str
    company: str
    role: str
    pending_questions: list[dict] = Field(default_factory=list)
    keyword_coverage: float
    application_id: int
    error: str


class AnswerItem(BaseModel):
    key: str
    question: str = ""
    answer: str


class AnswerBody(BaseModel):
    answers: list[AnswerItem] = Field(default_factory=list)


class FactOut(BaseModel):
    key: str
    question: str
    answer: str


class FactUpsert(BaseModel):
    key: str
    question: str = ""
    answer: str


def _item_out(i: BatchItem) -> BatchItemOut:
    return BatchItemOut(
        id=i.id,
        batch_id=i.batch_id or "",
        url=i.url or "",
        status=i.status or "",
        company=i.company or "",
        role=i.role or "",
        pending_questions=list(i.pending_questions or []),
        keyword_coverage=i.keyword_coverage if i.keyword_coverage is not None else -1.0,
        application_id=i.application_id or 0,
        error=i.error or "",
    )


# ---------- batch lifecycle ----------

@router.post("/batch")
def create_batch(payload: BatchCreate, db: Session = Depends(get_db)) -> dict:
    """Queue a URL list and kick off the worker."""
    urls = []
    seen = set()
    for u in payload.urls or []:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        if not u.lower().startswith(("http://", "https://")):
            continue
        seen.add(u)
        urls.append(u)
    if not urls:
        raise HTTPException(status_code=400, detail="No valid http(s) URLs provided.")

    batch_id = uuid.uuid4().hex[:12]
    for u in urls:
        db.add(BatchItem(batch_id=batch_id, url=u, status="queued"))
    db.commit()
    start_batch_async(batch_id)
    items = (
        db.query(BatchItem)
        .filter(BatchItem.batch_id == batch_id)
        .order_by(BatchItem.id)
        .all()
    )
    return {"batch_id": batch_id, "items": [_item_out(i).model_dump() for i in items]}


@router.get("/batch/latest")
def latest_batch(db: Session = Depends(get_db)) -> dict:
    newest = db.query(BatchItem).order_by(desc(BatchItem.id)).first()
    if newest is None:
        return {"batch_id": "", "items": []}
    return _batch_payload(db, newest.batch_id)


@router.get("/batch/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    return _batch_payload(db, batch_id)


def _batch_payload(db: Session, batch_id: str) -> dict:
    items = (
        db.query(BatchItem)
        .filter(BatchItem.batch_id == batch_id)
        .order_by(BatchItem.id)
        .all()
    )
    return {"batch_id": batch_id, "items": [_item_out(i).model_dump() for i in items]}


@router.post("/batch/answer")
def answer_questions(body: AnswerBody, db: Session = Depends(get_db)) -> dict:
    """Store answers as durable facts, then re-queue any parked items
    whose questions are now all answered, and resume the worker."""
    for a in body.answers or []:
        if not (a.answer or "").strip():
            continue
        facts_svc.upsert_fact(db, a.key, a.question, a.answer)

    known = facts_svc.all_facts(db)
    resumed = 0
    parked = db.query(BatchItem).filter(BatchItem.status == "needs_input").all()
    batch_ids: set[str] = set()
    for item in parked:
        qs = item.pending_questions or []
        unresolved = [q for q in qs if facts_svc.normalise_key(q.get("key", "")) not in known]
        if not unresolved:
            item.pending_questions = []
            item.status = "queued"
            resumed += 1
            batch_ids.add(item.batch_id)
        else:
            item.pending_questions = unresolved
    db.commit()
    for bid in batch_ids:
        start_batch_async(bid)
    return {"stored": len(body.answers or []), "resumed_items": resumed}


@router.post("/batch/item/{item_id}/retry")
def retry_item(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.query(BatchItem).filter(BatchItem.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    item.status = "queued"
    item.error = ""
    db.commit()
    start_batch_async(item.batch_id)
    return _item_out(item).model_dump()


# ---------- learned facts (memory) ----------

@router.get("/facts", response_model=list[FactOut])
def list_facts(db: Session = Depends(get_db)) -> list[FactOut]:
    rows = db.query(CandidateFact).order_by(CandidateFact.key).all()
    return [FactOut(key=r.key, question=r.question or "", answer=r.answer or "") for r in rows]


@router.post("/facts", response_model=FactOut)
def upsert_fact(body: FactUpsert, db: Session = Depends(get_db)) -> FactOut:
    if not (body.key or "").strip() or not (body.answer or "").strip():
        raise HTTPException(status_code=400, detail="key and answer are required.")
    r = facts_svc.upsert_fact(db, body.key, body.question, body.answer)
    return FactOut(key=r.key, question=r.question or "", answer=r.answer or "")


@router.delete("/facts/{key}")
def delete_fact(key: str, db: Session = Depends(get_db)) -> dict:
    ok = facts_svc.delete_fact(db, key)
    if not ok:
        raise HTTPException(status_code=404, detail="Fact not found.")
    return {"deleted": key}
