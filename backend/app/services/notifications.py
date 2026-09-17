"""Shared "notify the account's owner(s) about this store" logic, used by
both app/services/alerts.py and app/services/reports.py — extracted here
rather than duplicated since it's identical between the two: look up
recipients, check each one's per-channel preference, send email + push,
log-and-swallow a failure so one bad address/subscription doesn't block the
rest of a check/report run.
"""

import logging
from typing import Literal

from sqlalchemy.orm import Session

from app.email import send_email
from app.models import NotificationChannelPreference, Store, User
from app.services.push import send_push_to_user

logger = logging.getLogger("escal.notifications")

EventType = Literal["cac_alert", "roas_alert", "weekly_report"]


def recipients_for_store(db: Session, store: Store) -> list[User]:
    return db.query(User).filter_by(account_id=store.account_id, role="owner").all()


def send_to_store(db: Session, store: Store, subject: str, body: str, event_type: EventType) -> None:
    for user in recipients_for_store(db, store):
        # No saved row = both channels on, matching the behavior before this
        # preference table existed (see NotificationChannelPreference).
        pref = db.get(NotificationChannelPreference, (user.id, event_type))
        email_enabled = pref.email_enabled if pref else True
        push_enabled = pref.push_enabled if pref else True

        if email_enabled:
            try:
                send_email(user.email, subject, body)
            except Exception:
                logger.exception(
                    "store_notification_email_failed", extra={"store_id": str(store.id), "to": user.email}
                )
        if push_enabled:
            try:
                send_push_to_user(db, user.id, subject, body)
            except Exception:
                logger.exception(
                    "store_notification_push_failed", extra={"store_id": str(store.id), "user_id": str(user.id)}
                )
