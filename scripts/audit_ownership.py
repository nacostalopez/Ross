#!/usr/bin/env python
"""Ownership/encryption integrity audit for Ross.

Run locally against a live database (needs DATABASE_URL / .env set up same
as the backend):
    cd backend && python ../scripts/audit_ownership.py

Checks:
  1. Every store_credentials row's access_token/refresh_token decrypts with
     the current CREDENTIALS_ENCRYPTION_KEY (catches a rotated key that
     wasn't re-encrypted, or corrupted data).
  2. No store_credentials row references a store that no longer exists
     (should be impossible via the FK+cascade, but verified anyway).
  3. No orders/pixel_events/ad_spend row references a store_id with no
     matching store — these hypertables have no FK constraint enforcing
     this at the database level.

Exits non-zero if any check fails, so this can be wired into a cron/CI job
later without changes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from cryptography.fernet import InvalidToken
from sqlalchemy import text

from app.database import SessionLocal
from app.models import StoreCredential
from app.security import decrypt_secret


def audit_credential_encryption(db) -> list[str]:
    problems = []
    for cred in db.query(StoreCredential).all():
        for field_name, value in (("access_token", cred.access_token), ("refresh_token", cred.refresh_token)):
            if not value:
                continue
            try:
                decrypt_secret(value)
            except InvalidToken:
                problems.append(
                    f"store_credentials {cred.id} ({cred.provider}): {field_name} does not decrypt with "
                    "the current CREDENTIALS_ENCRYPTION_KEY"
                )
    return problems


def audit_orphaned_credentials(db) -> list[str]:
    rows = db.execute(text(
        "SELECT sc.id, sc.provider FROM store_credentials sc "
        "LEFT JOIN stores s ON s.id = sc.store_id WHERE s.id IS NULL"
    )).all()
    return [f"store_credentials {row.id} ({row.provider}) references a store that no longer exists" for row in rows]


def audit_orphaned_timeseries(db) -> list[str]:
    problems = []
    for table in ("orders", "pixel_events", "ad_spend"):
        rows = db.execute(text(
            f"SELECT DISTINCT t.store_id FROM {table} t "
            "LEFT JOIN stores s ON s.id = t.store_id WHERE s.id IS NULL"
        )).all()
        for row in rows:
            problems.append(f"{table} has rows for store_id {row.store_id}, which no longer exists in stores")
    return problems


def main() -> int:
    db = SessionLocal()
    try:
        problems = [
            *audit_credential_encryption(db),
            *audit_orphaned_credentials(db),
            *audit_orphaned_timeseries(db),
        ]
    finally:
        db.close()

    if problems:
        print(f"FAILED - {len(problems)} issue(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("OK - no ownership/encryption integrity issues found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
