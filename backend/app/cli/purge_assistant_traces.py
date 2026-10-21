"""Delete the assistant's traces and events older than the retention period (180 days).

There's no in-process scheduler in the FastAPI app; the Railway cron service
"cron" runs this daily (see DEPLOY.md); locally:

    docker compose exec backend python -m app.cli.purge_assistant_traces

It logs only the counts of rows deleted, never ids or content.
"""

import logging

from app.ai.traces import purge_expired
from app.config import settings
from app.database import SessionLocal
from app.logging_config import configure_logging

logger = logging.getLogger("ross.assistant")


def main(session_factory=SessionLocal) -> dict[str, int]:
    configure_logging(settings.log_level)
    db = session_factory()
    try:
        counts = purge_expired(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    logger.info("assistant_traces_purged", extra={"traces": counts["traces"], "events": counts["events"]})
    return counts


if __name__ == "__main__":
    main()
