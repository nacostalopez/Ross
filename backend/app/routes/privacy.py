"""The signed-in person's own data: see it, and have it erased.

These are the channels for the rights of access and erasure of the people who use ROSS itself. The data of
a CRM's contacts is a separate matter (the account answers for it), handled with the CRM module.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import effective_roles_for_stores, get_current_user, require_role
from app.models import Account, AccountActivityLog, AssistantEvent, AssistantTrace, RefreshToken, Store, User
from app.rate_limit import limiter
from app.schemas.privacy import (
    DeleteAccountIn,
    DeleteMeIn,
    PrivacyActivityOut,
    PrivacyAssistantEventOut,
    PrivacyExportOut,
    PrivacyMembershipOut,
    PrivacyUserOut,
)
from app.security import verify_password
from app.services.account_deletion import AccountDeletionBlocked, delete_account

router = APIRouter(prefix="/privacy", tags=["privacy"])
logger = logging.getLogger("ross.privacy")

WRONG_PASSWORD = "La contraseña no coincide."


def _check_password(user: User, password: str) -> None:
    if not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=WRONG_PASSWORD)


@router.get("/export", response_model=PrivacyExportOut)
def export_my_data(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything kept about the signed-in person, as a downloadable file."""
    account = db.get(Account, current_user.account_id)
    stores = db.query(Store).filter(Store.account_id == current_user.account_id).all()
    roles = effective_roles_for_stores(db, current_user, [store.id for store in stores])
    activity = (
        db.query(AccountActivityLog)
        .filter(AccountActivityLog.user_id == current_user.id)
        .order_by(AccountActivityLog.created_at.desc())
        .all()
    )

    events = (
        db.query(AssistantTrace.tool, AssistantEvent.kind, AssistantEvent.created_at)
        .join(AssistantTrace, AssistantTrace.id == AssistantEvent.trace_id)
        .filter(AssistantEvent.user_id == current_user.id, AssistantEvent.account_id == current_user.account_id)
        .order_by(AssistantEvent.created_at.desc())
        .all()
    )

    response.headers["Content-Disposition"] = 'attachment; filename="ross-mis-datos.json"'
    return PrivacyExportOut(
        generated_at=datetime.now(timezone.utc),
        user=PrivacyUserOut(
            id=current_user.id,
            email=current_user.email,
            role=current_user.role,
            created_at=current_user.created_at,
            terms_accepted_at=current_user.terms_accepted_at,
            terms_version=current_user.terms_version,
        ),
        account_name=account.name if account else None,
        store_memberships=[
            PrivacyMembershipOut(store_id=store.id, store_name=store.name, role=roles[store.id]) for store in stores
        ],
        activity=[PrivacyActivityOut(action=a.action, detail=a.detail, created_at=a.created_at) for a in activity],
        assistant_events=[PrivacyAssistantEventOut(tool=t, kind=k, created_at=c) for t, k, c in events],
    )


@router.post("/delete-account", status_code=204)
@limiter.limit("5/minute")
def delete_my_account(
    request: Request,
    payload: DeleteAccountIn,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Delete the whole account: its users, stores and every piece of data. Only an owner, and only with
    their password and the word ELIMINAR typed in full."""
    _check_password(current_user, payload.password)
    account = db.get(Account, current_user.account_id)
    try:
        delete_account(db, account)
    except AccountDeletionBlocked as blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(blocked))


@router.post("/delete-me", status_code=204)
@limiter.limit("5/minute")
def delete_myself(
    request: Request,
    payload: DeleteMeIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete the signed-in person's own user, leaving the account to the others. The last owner can't:
    the account would be left with nobody in charge, so they delete the account or hand it over first."""
    _check_password(current_user, payload.password)

    if current_user.role == "owner":
        other_owners = (
            db.query(User)
            .filter(User.account_id == current_user.account_id, User.role == "owner", User.id != current_user.id)
            .count()
        )
        if other_owners == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Sos el único propietario de la cuenta: eliminá la cuenta completa o nombrá a otro propietario antes.",
            )

    account_id = current_user.account_id
    db.query(RefreshToken).filter(
        RefreshToken.user_id == current_user.id, RefreshToken.revoked_at.is_(None)
    ).update({"revoked_at": datetime.now(timezone.utc)})
    db.delete(current_user)
    db.commit()
    logger.info("user_deleted_self", extra={"account_id": str(account_id)})
