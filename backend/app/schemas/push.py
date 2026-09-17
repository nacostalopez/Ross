from pydantic import BaseModel


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    # Matches PushSubscription.toJSON() from the browser's Push API
    # (endpoint/keys.p256dh/keys.auth) — the frontend posts that object
    # as-is, no reshaping needed.
    endpoint: str
    keys: PushKeys


class VapidPublicKeyOut(BaseModel):
    # Empty string means push isn't configured server-side (no VAPID keys
    # set) — the frontend uses that to hide the notification toggle
    # instead of offering a subscribe flow that can never actually send.
    public_key: str
