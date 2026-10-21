from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.tools import DenialReason, StoreNotFound, resolve_context, run_tool
from app.database import get_db
from app.dependencies import get_current_user, get_owned_store
from app.models import Store, User
from app.schemas.narrative import NarrativeOut

router = APIRouter(prefix="/stores/{store_id}/narrative", tags=["narrative"])


@router.get("", response_model=NarrativeOut)
def weekly_narrative(
    store: Store = Depends(get_owned_store),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ross explica tu semana: the last 7 full days against the 7 before, in plain language.

    Goes through the assistant's tool registry, so each visit leaves a trace like any other tool run."""
    try:
        ctx = resolve_context(db, current_user, store.id)
    except StoreNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    result = run_tool("weekly_narrative", ctx)
    if not result.ok:
        code = status.HTTP_403_FORBIDDEN if result.denial is not DenialReason.UNKNOWN_TOOL else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=result.message)
    return result.data
