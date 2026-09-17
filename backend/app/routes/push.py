from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import PushSubscription, User
from app.schemas.push import PushSubscriptionIn, VapidPublicKeyOut
from app.services.push import PushSettings

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyOut)
def get_vapid_public_key(_: User = Depends(get_current_user)):
    """Empty string means push isn't configured server-side (no VAPID keys
    set yet) — the frontend uses that to hide the notification toggle
    instead of offering a subscribe flow that could never actually send."""
    return VapidPublicKeyOut(public_key=PushSettings().vapid_public_key)


@router.post("/subscribe", status_code=204)
def subscribe(
    payload: PushSubscriptionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upserts on (user, endpoint) — a device that already subscribed and
    calls this again (e.g. the browser rotated the subscription's keys)
    just updates the existing row instead of creating a duplicate."""
    existing = db.query(PushSubscription).filter_by(user_id=user.id, endpoint=payload.endpoint).first()
    if existing:
        existing.p256dh_key = payload.keys.p256dh
        existing.auth_key = payload.keys.auth
    else:
        db.add(
            PushSubscription(
                id=uuid4(),
                user_id=user.id,
                endpoint=payload.endpoint,
                p256dh_key=payload.keys.p256dh,
                auth_key=payload.keys.auth,
            )
        )
    db.commit()


@router.delete("/subscribe", status_code=204)
def unsubscribe(endpoint: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(PushSubscription).filter_by(user_id=user.id, endpoint=endpoint).delete()
    db.commit()
