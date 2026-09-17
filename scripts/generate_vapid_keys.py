#!/usr/bin/env python
"""Generate a VAPID keypair for Web Push notifications (see
backend/app/services/push.py).

Run once, then paste the output into your root .env:

    python scripts/generate_vapid_keys.py >> .env

Without VAPID_PRIVATE_KEY/VAPID_PUBLIC_KEY set, push silently no-ops (see
PushSettings in app/services/push.py) — every other feature works fine
without them, this is purely opt-in. Only needs the `cryptography` package
(already a backend dependency), no network access.
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_numbers = private_key.private_numbers()
    public_numbers = private_numbers.public_numbers

    # Raw scalar (32 bytes) and raw uncompressed point (0x04 || X || Y, 65
    # bytes) — the format both pywebpush (backend) and the browser's
    # PushManager.subscribe({ applicationServerKey }) expect.
    private_raw = private_numbers.private_value.to_bytes(32, "big")
    public_raw = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")

    print(f"VAPID_PRIVATE_KEY={_b64url(private_raw)}")
    print(f"VAPID_PUBLIC_KEY={_b64url(public_raw)}")
    print("# change this to an inbox you actually monitor:")
    print("VAPID_CLAIM_EMAIL=admin@yourdomain.com")


if __name__ == "__main__":
    main()
