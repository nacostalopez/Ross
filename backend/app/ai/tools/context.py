"""Building the context a tool runs in, from the signed-in user."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.tools.base import ToolContext
from app.dependencies import effective_roles_for_stores, find_owned_store
from app.models import User


class StoreNotFound(Exception):
    """The store doesn't exist or isn't in the user's account. Deliberately the same for both."""


def resolve_context(
    db: Session,
    user: User,
    store_id: UUID,
    now: Optional[datetime] = None,
    confirmed: bool = False,
) -> ToolContext:
    """The only way to get a ToolContext: the store must belong to the user's account.

    Uses the same rule as the API's get_owned_store, and the role is the one the user holds on that
    store, read from the database each time so a role change applies at once.
    """
    store = find_owned_store(db, user, store_id)
    if store is None:
        raise StoreNotFound()
    role = effective_roles_for_stores(db, user, [store.id])[store.id]
    return ToolContext(db=db, user=user, store=store, role=role, now=now or datetime.now(timezone.utc), confirmed=confirmed)
