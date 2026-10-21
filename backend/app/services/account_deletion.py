"""Deleting an account for good, and everything that belongs to it.

Most of an account's data goes with it through ON DELETE CASCADE. The exception is the data keyed by
`store_id` without a foreign key (the orders, ad spend, pixel events and creative performance
hypertables), which would be left behind as orphans, so it is removed here first. The tables are found
from the database itself, so a table added later with a `store_id` is cleaned up without touching this.

Anything added with an `account_id` has to reference `accounts` with ON DELETE CASCADE; a test fails
if a table doesn't.
"""

import logging
import re
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Account, Store, Subscription

logger = logging.getLogger("ross.privacy")

_SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_LIVE_STATUSES = ("active", "trialing", "past_due")


class AccountDeletionBlocked(Exception):
    """The account can't be deleted yet; the message says what to do first."""


def tables_with_column(db: Session, column: str) -> list[str]:
    rows = db.execute(
        text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name = :column ORDER BY table_name"
        ),
        {"column": column},
    ).scalars()
    return [name for name in rows if _SAFE_NAME.match(name)]


def check_can_delete(db: Session, account_id: UUID) -> None:
    """A paid subscription keeps charging after the account is gone, so it has to be cancelled first."""
    subscription: Optional[Subscription] = db.query(Subscription).filter_by(account_id=account_id).first()
    if subscription and subscription.provider_subscription_id and subscription.status in _LIVE_STATUSES:
        raise AccountDeletionBlocked("Cancelá tu suscripción antes de eliminar la cuenta, para que no se siga cobrando.")


def delete_account(db: Session, account: Account) -> None:
    """Delete the account, its users, stores and all their data, in one transaction."""
    check_can_delete(db, account.id)
    account_id = account.id

    store_ids = [str(store_id) for (store_id,) in db.query(Store.id).filter(Store.account_id == account_id)]
    if store_ids:
        for table in tables_with_column(db, "store_id"):
            db.execute(text(f'DELETE FROM "{table}" WHERE store_id = ANY(CAST(:ids AS uuid[]))'), {"ids": store_ids})

    db.delete(account)
    db.commit()
    # Only the id: nothing about who the people were.
    logger.info("account_deleted", extra={"account_id": str(account_id), "stores": len(store_ids)})
