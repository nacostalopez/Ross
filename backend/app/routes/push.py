from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import PushSubscription, User
from app.schemas.push import PushSubscriptionIn, PushSubscriptionOut, VapidPublicKeyOut
from app.services.push import PushSettings

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyOut)
def get_vapid_public_key(_: User = Depends(get_current_user)):
    """Empty string means push isn't configured server-side (no VAPID keys
    set yet) — the frontend uses that to hide the notification toggle
    instead of offering a subscribe flow that could never actually send."""
    return VapidPublicKeyOut(public_key=PushSettings().vapid_public_key)


def _label_from_user_agent(user_agent: str | None) -> str:
    """A rough, good-enough device label for "Dispositivos y sesiones" — the
    Push API itself exposes no device name, so this is the same coarse
    User-Agent sniffing every "your active sessions" screen does."""
    if not user_agent:
        return "Dispositivo"
    ua = user_agent.lower()
    if "iphone" in ua:
        os_label = "iPhone"
    elif "ipad" in ua:
        os_label = "iPad"
    elif "android" in ua:
        os_label = "Android"
    elif "windows" in ua:
        os_label = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        os_label = "Mac"
    elif "linux" in ua:
        os_label = "Linux"
    else:
        os_label = "Dispositivo"

    if "edg/" in ua:
        browser = "Edge"
    elif "chrome/" in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "Safari"
    else:
        browser = None

    return f"{os_label} · {browser}" if browser else os_label


@router.post("/subscribe", status_code=204)
def subscribe(
    payload: PushSubscriptionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upserts on (user, endpoint) — a device that already subscribed and
    calls this again (e.g. the browser rotated the subscription's keys)
    just updates the existing row instead of creating a duplicate."""
    user_agent = request.headers.get("user-agent")
    existing = db.query(PushSubscription).filter_by(user_id=user.id, endpoint=payload.endpoint).first()
    if existing:
        existing.p256dh_key = payload.keys.p256dh
        existing.auth_key = payload.keys.auth
        existing.user_agent = user_agent
    else:
        db.add(
            PushSubscription(
                id=uuid4(),
                user_id=user.id,
                endpoint=payload.endpoint,
                p256dh_key=payload.keys.p256dh,
                auth_key=payload.keys.auth,
                user_agent=user_agent,
            )
        )
    db.commit()


@router.delete("/subscribe", status_code=204)
def unsubscribe(endpoint: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(PushSubscription).filter_by(user_id=user.id, endpoint=endpoint).delete()
    db.commit()


@router.get("/subscriptions", response_model=list[PushSubscriptionOut])
def list_subscriptions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every device currently subscribed for this user — unlike DELETE
    /push/subscribe (which only ever unsubscribes the *calling* browser's
    own subscription), this is how a user revokes a *different* device from
    "Dispositivos y sesiones"."""
    rows = (
        db.query(PushSubscription)
        .filter_by(user_id=user.id)
        .order_by(PushSubscription.created_at.desc())
        .all()
    )
    return [
        PushSubscriptionOut(id=row.id, label=_label_from_user_agent(row.user_agent), created_at=row.created_at)
        for row in rows
    ]


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def revoke_subscription(subscription_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revokes a device remotely — that device's own browser is never told,
    it just stops receiving pushes (the row it would have been matched
    against is gone), the same way revoking a refresh token elsewhere in
    this app doesn't notify the other session either."""
    row = db.get(PushSubscription, subscription_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    db.delete(row)
    db.commit()
