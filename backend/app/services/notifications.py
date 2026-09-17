"""Shared "notify the account's owner(s) about this store" logic, used by
both app/services/alerts.py and app/services/reports.py — extracted here
rather than duplicated since it's identical between the two: look up
recipients, send email + push, log-and-swallow a failure so one bad
address/subscription doesn't block the rest of a check/report run.
"""

import logging

from sqlalchemy.orm import Session

from app.email import send_email
from app.models import Store, User
from app.services.push import send_push_to_user

logger = logging.getLogger("escal.notifications")


def recipients_for_store(db: Session, store: Store) -> list[User]:
    return db.query(User).filter_by(account_id=store.account_id, role="owner").all()


def send_to_store(db: Session, store: Store, subject: str, body: str) -> None:
    for user in recipients_for_store(db, store):
        try:
            send_email(user.email, subject, body)
        except Exception:
            logger.exception("store_notification_email_failed", extra={"store_id": str(store.id), "to": user.email})
        try:
            send_push_to_user(db, user.id, subject, body)
        except Exception:
            logger.exception(
                "store_notification_push_failed", extra={"store_id": str(store.id), "user_id": str(user.id)}
            )
