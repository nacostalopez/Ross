from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Plan, Store, StoreMembership, Subscription, User
from app.security import decode_access_token

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_access_token(credentials.credentials)
    user = db.get(User, payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user


def get_owned_store(
    store_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Store:
    store = db.get(Store, store_id)
    if not store or store.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return store


def require_role(*allowed_roles: str):
    """Gate a route to specific roles, e.g. Depends(require_role("owner", "admin")).

    Reads role straight off the User row get_current_user already fetches —
    no extra query. Deliberately not read from the JWT: tokens are long-lived
    (7 days) with no revocation, so trusting a baked-in role claim would let
    a demoted/removed user keep old permissions for up to a week. Re-checking
    against the DB on every request means a role change takes effect on the
    next request instead.
    """
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return _check


def _effective_role_for_store(db: Session, user: User, store_id: UUID) -> str:
    """A StoreMembership row overrides the account-wide role for that one
    store; no row means the account role applies, same as before this
    feature existed. Always a live query, same reasoning as require_role's
    own docstring above (a role change — account-wide or per-store — must
    take effect on the next request, not wait out a cached value)."""
    override = db.get(StoreMembership, (store_id, user.id))
    return override.role if override else user.role


def effective_roles_for_stores(db: Session, user: User, store_ids: list[UUID]) -> dict[UUID, str]:
    """The role `user` holds on each of `store_ids` — the same rule as
    _effective_role_for_store, but one query for all of them instead of one
    per store (GET /stores would otherwise be N+1). Also a live query, for
    the same reason."""
    if not store_ids:
        return {}
    overrides = {
        membership.store_id: membership.role
        for membership in db.query(StoreMembership)
        .filter(StoreMembership.user_id == user.id, StoreMembership.store_id.in_(store_ids))
        .all()
    }
    return {store_id: overrides.get(store_id, user.role) for store_id in store_ids}


def require_store_role(*allowed_roles: str):
    """Like require_role, but checks the role effective *for this store*
    (store_id is a path param FastAPI injects the same way get_owned_store
    already does) rather than the account-wide role unconditionally.

    Only for store-scoped routes (prefix /stores/{store_id}/...) — account-
    level actions (managing account members/invites in app/routes/accounts.py)
    stay on plain require_role, since a store override has no business
    affecting cuenta-wide administration.
    """
    def _check(
        store_id: UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        role = _effective_role_for_store(db, current_user, store_id)
        if role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return _check


def _plan_for_account(db: Session, account_id: UUID) -> Plan:
    """No Subscription row = "scale" (every feature) — every account gets
    one at registration (see app/routes/auth.py::register) and existing
    accounts were grandfathered onto it by db/init/028_billing.sql, so this
    fallback is really just belt-and-suspenders, not the normal path. See
    that migration / app/models/billing.py for why "scale" (not "starter")
    is the safe default: there's no paid checkout flow yet, so gating
    anyone down to Starter today would strand them with no way to upgrade.
    """
    sub = db.query(Subscription).filter_by(account_id=account_id).first()
    plan_id = sub.plan_id if sub else "scale"
    return db.get(Plan, plan_id)


def require_plan_feature(feature: str):
    """Gate a route behind a plan's feature list, e.g.
    Depends(require_plan_feature("forecast")). A feature key not present
    in ANY plan's `features` column is a bug in how this was called, not a
    valid "nobody has it" state — every gated feature must be listed on at
    least "scale" (db/init/028_billing.sql) or no account could ever use it.
    """
    def _check(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        plan = _plan_for_account(db, current_user.account_id)
        if feature not in plan.features:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Esta funcionalidad requiere un plan superior a {plan.name}",
            )
        return current_user
    return _check
