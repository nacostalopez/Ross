from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_owned_store
from app.models import Store
from app.schemas.narrative import NarrativeOut
from app.services.weekly_narrative import get_or_build

router = APIRouter(prefix="/stores/{store_id}/narrative", tags=["narrative"])


@router.get("", response_model=NarrativeOut)
def weekly_narrative(store: Store = Depends(get_owned_store), db: Session = Depends(get_db)):
    """Ross explica tu semana: the last 7 full days against the 7 before, in plain language."""
    return get_or_build(db, store)
