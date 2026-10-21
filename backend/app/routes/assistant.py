"""What people say about the assistant's answers, and what that says about each tool (docs/ia/fase-4-2-diseno.md)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.ai import traces
from app.ai.tools import get_tool
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import User
from app.rate_limit import limiter
from app.schemas.assistant import AssistantEventIn, UtilityScoreOut

router = APIRouter(prefix="/assistant", tags=["assistant"])

# One message for a trace that doesn't exist and one that belongs to another account.
TRACE_NOT_FOUND = "Respuesta no encontrada"


@router.post("/traces/{trace_id}/events", status_code=204)
@limiter.limit("120/minute")
def mark_trace(
    request: Request,
    trace_id: UUID,
    payload: AssistantEventIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark an answer as shown, useful, not useful or dismissed. Any signed-in role; the demo account is
    refused (403) by get_current_user before this runs, so it never writes."""
    event = traces.record_event(db, user=current_user, trace_id=trace_id, kind=payload.kind)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TRACE_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tools/{tool}/score", response_model=UtilityScoreOut)
def tool_score(
    tool: str,
    tier: str = Query("L0", pattern=r"^L\d$"),
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """How useful the account's people found a tool over the last 30 days. Only this account's data."""
    if get_tool(tool) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Herramienta no encontrada")
    score = traces.utility_score(db, account_id=current_user.account_id, tool=tool, tier=tier)
    return UtilityScoreOut(
        tool=score.tool,
        tier=score.tier,
        useful=score.useful,
        not_useful=score.not_useful,
        samples=score.samples,
        runs=score.runs,
        validator_rejection_rate=score.validator_rejection_rate,
        score=score.score,
        status=score.status,
    )
