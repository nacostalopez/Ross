from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_owned_store, require_plan_feature, require_role, require_store_role
from app.models import Store, StoreCredential, StoreMembership, User
from app.schemas.stores import (
    StoreCreate,
    StoreCredentialCreate,
    StoreCredentialOut,
    StoreMemberOut,
    StoreMemberRoleIn,
    StoreOut,
)
from app.security import encrypt_secret
from app.services.activity_log import log_activity

router = APIRouter(prefix="/stores", tags=["stores"])


@router.post("", response_model=StoreOut, status_code=201)
def create_store(
    payload: StoreCreate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    store = Store(account_id=current_user.account_id, **payload.model_dump())
    db.add(store)
    db.commit()
    db.refresh(store)
    log_activity(db, current_user.account_id, current_user.id, "store_created", f"Creaste la tienda {store.name}")
    return store


@router.get("", response_model=list[StoreOut])
def list_stores(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Store)
        .filter(Store.account_id == current_user.account_id)
        .order_by(Store.created_at.desc())
        .all()
    )


@router.get("/{store_id}", response_model=StoreOut)
def get_store(store: Store = Depends(get_owned_store)):
    return store


@router.put("/{store_id}/credentials", response_model=StoreCredentialOut)
def upsert_credentials(
    payload: StoreCredentialCreate,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    data = payload.model_dump()
    data["access_token"] = encrypt_secret(data["access_token"])
    if data.get("refresh_token"):
        data["refresh_token"] = encrypt_secret(data["refresh_token"])

    stmt = (
        pg_insert(StoreCredential.__table__)
        .values(store_id=store.id, **data)
        .on_conflict_do_update(
            index_elements=["store_id", "provider"],
            set_=data,
        )
        .returning(StoreCredential.__table__.c.id)
    )
    result = db.execute(stmt)
    db.commit()
    row_id = result.scalar_one()
    return db.get(StoreCredential, row_id)


def _store_member_out(member: User, override: StoreMembership | None) -> StoreMemberOut:
    return StoreMemberOut(
        id=member.id,
        email=member.email,
        account_role=member.role,
        store_role=override.role if override else None,
        effective_role=override.role if override else member.role,
    )


@router.get("/{store_id}/members", response_model=list[StoreMemberOut])
def list_store_members(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Account-owner only — same reasoning as set_store_member_role below:
    granting/viewing per-store overrides is account-level administration,
    not something a store-scoped role (even one granted via an override)
    should control."""
    members = db.query(User).filter(User.account_id == store.account_id).order_by(User.created_at.asc()).all()
    overrides = {
        m.user_id: m for m in db.query(StoreMembership).filter(StoreMembership.store_id == store.id).all()
    }
    return [_store_member_out(member, overrides.get(member.id)) for member in members]


@router.put("/{store_id}/members/{user_id}", response_model=StoreMemberOut)
def set_store_member_role(
    user_id: UUID,
    payload: StoreMemberRoleIn,
    store: Store = Depends(get_owned_store),
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Gated to the account-wide owner role (require_role, not
    require_store_role) — granting a per-store override is a sensitive,
    account-level permissions action. Gating it with require_store_role
    instead would let a user who only holds "admin" via a store override
    grant themselves (or anyone) a stronger override on that same store."""
    member = db.get(User, user_id)
    if not member or member.account_id != store.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    existing = db.get(StoreMembership, (store.id, user_id))
    if payload.role is None:
        # Clearing an override only ever *reduces* access — never gated.
        if existing:
            db.delete(existing)
            db.commit()
        return _store_member_out(member, None)

    require_plan_feature("store_role_overrides")(current_user, db)

    if existing:
        existing.role = payload.role
    else:
        existing = StoreMembership(store_id=store.id, user_id=user_id, role=payload.role)
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return _store_member_out(member, existing)
