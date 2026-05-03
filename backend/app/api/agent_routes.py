"""Agent orchestrator endpoint: full Profile → Tailoring pipeline."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.schemas import AgentRunRequest, AgentRunResponse
from app.services.agent_orchestrator import run_agent, to_response

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/run", response_model=AgentRunResponse)
def agent_run(
    payload: AgentRunRequest | None = None,
    db: Session = Depends(get_db),
) -> AgentRunResponse:
    """Run the end-to-end pipeline:

        Profile  → Tags/Queries → Discovery → Ranking → Tailoring

    All steps are best-effort: a failure in any non-fatal step is
    recorded in `steps[]` with status `"error"` / `"skipped"` and the
    pipeline still returns whatever data made it through. Fatal errors
    (no profile **and** no CVs to build one from, empty CV pool) set
    `error` and short-circuit, but the partial trace is still returned.
    """
    body = payload or AgentRunRequest()
    state = run_agent(
        db,
        sources=body.sources,
        max_discover=body.max_discover,
        max_rank=body.max_rank,
        max_tailor=body.max_tailor,
        cv_ids=body.cv_ids,
        use_profile_fallback=body.use_profile_fallback,
        queries_override=body.queries,
        tags_override=body.tags.model_dump() if body.tags else None,
    )
    return to_response(state)
