import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.email import send_email
from app.models import AccountActivityLog, AccountInvite, RefreshToken, User
from app.routes.auth import issue_tokens
from app.schemas.accounts import (
    AccountOut,
    ActivityLogEntryOut,
    InviteAcceptIn,
    InviteCreate,
    InviteOut,
    MemberOut,
    RoleUpdateIn,
)
from app.schemas.auth import TokenOut
from app.security import hash_password, hash_token
from app.services.activity_log import log_activity

router = APIRouter(prefix="/accounts", tags=["accounts"])
logger = logging.getLogger("ross.accounts")

INVITE_EXPIRY_DAYS = 7


def _send_invite_email(invite: AccountInvite, inviter: User, raw_token: str, *, reminder: bool = False) -> None:
    """Shared by create_invite and resend_invite — a send failure is caught
    and logged, never raised, since the raw token in the API response is
    still a usable fallback for the caller."""
    invite_url = f"{settings.frontend_url}/index.html?invite_token={raw_token}"
    subject_prefix = "Reminder: y" if reminder else "Y"
    try:
        send_email(
            to=invite.email,
            subject=f"{subject_prefix}ou've been invited to {inviter.account.name} on Ross",
            body=(
                f"{inviter.email} invited you to join {inviter.account.name} "
                f"on Ross as {invite.role}.\n\n"
                f"Accept your invite: {invite_url}\n\n"
                f"Or use this token directly: {raw_token}\n\n"
                f"This invite expires in {INVITE_EXPIRY_DAYS} days."
            ),
        )
    except Exception:
        logger.exception("invite_email_send_failed", extra={"invite_id": str(invite.id), "email": invite.email})


@router.get("/me", response_model=AccountOut)
def get_my_account(current_user: User = Depends(get_current_user)):
    return current_user.account


@router.get("/activity", response_model=list[ActivityLogEntryOut])
def list_my_activity(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Personal, cross-store feed — every role can see their own actions;
    this is a "what did I just do" list, not an admin audit trail (see
    AccountActivityLog's docstring for why it only covers a handful of
    actions)."""
    return (
        db.query(AccountActivityLog)
        .filter_by(user_id=current_user.id)
        .order_by(AccountActivityLog.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/members", response_model=list[MemberOut])
def list_members(
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    return db.query(User).filter(User.account_id == current_user.account_id).order_by(User.created_at.asc()).all()


@router.post("/invites", response_model=InviteOut, status_code=201)
def create_invite(
    payload: InviteCreate,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    existing_pending = (
        db.query(AccountInvite)
        .filter_by(
            account_id=current_user.account_id,
            email=payload.email,
            status="pending",
        )
        .first()
    )
    if existing_pending:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An invite to this email is already pending")

    raw_token = secrets.token_urlsafe(32)

    invite = AccountInvite(
        id=uuid4(),
        account_id=current_user.account_id,
        email=payload.email,
        role=payload.role,
        invited_by=current_user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    _send_invite_email(invite, current_user, raw_token)
    log_activity(
        db, current_user.account_id, current_user.id, "invite_sent", f"Invitaste a {invite.email} como {invite.role}"
    )

    result = InviteOut.model_validate(invite)
    result.token = raw_token
    return result


@router.post("/invites/{invite_id}/resend", response_model=InviteOut)
def resend_invite(
    invite_id: UUID,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Re-sends the invite email with a fresh token and a fresh expiry —
    covers both "I lost the email" and "it expired before I opened it"
    (an expired invite's status stays 'pending' until accepted or revoked,
    so it's still resendable)."""
    invite = db.get(AccountInvite, invite_id)
    if not invite or invite.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only pending invites can be resent")

    raw_token = secrets.token_urlsafe(32)
    invite.token_hash = hash_token(raw_token)
    invite.expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)
    db.commit()
    db.refresh(invite)

    _send_invite_email(invite, current_user, raw_token, reminder=True)

    result = InviteOut.model_validate(invite)
    result.token = raw_token
    return result


@router.get("/invites", response_model=list[InviteOut])
def list_invites(
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    return (
        db.query(AccountInvite)
        .filter(AccountInvite.account_id == current_user.account_id, AccountInvite.status == "pending")
        .order_by(AccountInvite.created_at.desc())
        .all()
    )


@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(
    invite_id: UUID,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    invite = db.get(AccountInvite, invite_id)
    if not invite or invite.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    invite.status = "revoked"
    db.commit()


@router.post("/invites/accept", response_model=TokenOut)
def accept_invite(payload: InviteAcceptIn, db: Session = Depends(get_db)):
    invite = db.query(AccountInvite).filter_by(token_hash=hash_token(payload.token)).first()

    now = datetime.now(timezone.utc)
    if not invite or invite.status != "pending" or invite.expires_at < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invite")

    if db.query(User).filter(User.email == invite.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        id=uuid4(),
        account_id=invite.account_id,
        email=invite.email,
        hashed_password=hash_password(payload.password),
        role=invite.role,
    )
    db.add(user)

    invite.status = "accepted"
    invite.accepted_at = now

    db.commit()
    db.refresh(user)

    return issue_tokens(db, user)


@router.patch("/members/{user_id}/role", response_model=MemberOut)
def update_member_role(
    user_id: UUID,
    payload: RoleUpdateIn,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    member = db.get(User, user_id)
    if not member or member.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if member.role == "owner" and payload.role != "owner":
        remaining_owners = (
            db.query(User)
            .filter(
                User.account_id == current_user.account_id,
                User.role == "owner",
                User.id != member.id,
            )
            .count()
        )
        if remaining_owners == 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot demote the last owner")

    member.role = payload.role
    db.commit()
    db.refresh(member)
    return member


@router.delete("/members/{user_id}", status_code=204)
def remove_member(
    user_id: UUID,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    member = db.get(User, user_id)
    if not member or member.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if member.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove yourself")

    if member.role == "owner":
        remaining_owners = (
            db.query(User)
            .filter(
                User.account_id == current_user.account_id,
                User.role == "owner",
                User.id != member.id,
            )
            .count()
        )
        if remaining_owners == 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot remove the last owner")

    db.query(RefreshToken).filter(
        RefreshToken.user_id == member.id,
        RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": datetime.now(timezone.utc)})

    db.delete(member)
    db.commit()
