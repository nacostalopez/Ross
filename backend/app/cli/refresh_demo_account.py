"""Create or refresh the public read-only demo account (see
app/services/demo_account.py). Needs DEMO_VIEWER_EMAIL; run it daily so the
demo always shows the last 90 days:

    docker compose exec backend python -m app.cli.refresh_demo_account
"""

import sys

from app.config import settings
from app.database import SessionLocal
from app.services.demo_account import refresh_demo_account


def main() -> int:
    if not settings.demo_viewer_email:
        print("DEMO_VIEWER_EMAIL is not set; the demo is disabled. Set it and run this again.")
        return 1
    with SessionLocal() as db:
        store_id = refresh_demo_account(db, settings.demo_viewer_email).id
    print(f"Demo refreshed: store {store_id} for {settings.demo_viewer_email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
