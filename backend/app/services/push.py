"""Web Push (browser push, delivered through the frontend's service worker)
— same colocated-settings/no-op-when-unconfigured pattern as app/email.py:
VAPID_PRIVATE_KEY unset means send_push_to_user() just logs instead of
sending, so local dev/tests never need real keys. Generate a keypair with
scripts/generate_vapid_keys.py.
"""

import json
import logging
from uuid import UUID

from pydantic_settings import BaseSettings
from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app.models import PushSubscription

logger = logging.getLogger("ross.push")


class PushSettings(BaseSettings):
    vapid_private_key: str = ""  # empty = not configured, send_push_to_user() no-ops (logs only)
    vapid_public_key: str = ""
    vapid_claim_email: str = "admin@escal.app"

    class Config:
        env_file = ".env"


def send_push_to_user(db: Session, user_id: UUID, title: str, body: str) -> None:
    settings = PushSettings()
    if not settings.vapid_private_key:
        logger.info("push_not_sent_no_vapid_configured", extra={"user_id": str(user_id), "title": title})
        return

    subs = db.query(PushSubscription).filter_by(user_id=user_id).all()
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
                },
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
            )
            logger.info("push_sent", extra={"user_id": str(user_id), "endpoint": sub.endpoint})
        except WebPushException as exc:
            # 404/410 = the browser dropped this subscription (data cleared,
            # uninstalled, expired) — stop trying it instead of erroring on
            # every future alert/report for this user.
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (404, 410):
                logger.info(
                    "push_subscription_gone_pruned",
                    extra={"user_id": str(user_id), "endpoint": sub.endpoint, "status_code": status_code},
                )
                db.delete(sub)
                db.commit()
            else:
                logger.exception("push_send_failed", extra={"user_id": str(user_id), "endpoint": sub.endpoint})
