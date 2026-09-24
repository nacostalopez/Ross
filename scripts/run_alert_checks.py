#!/usr/bin/env python
"""Run the proactive CAC/ROAS alert check for every store that opted in.

There's no in-process scheduler in the FastAPI app (see
app/services/alerts.py's module docstring for why) — this script is meant
to be invoked by a real cron instead:

    # crontab -e, once a day at 09:00
    0 9 * * * cd /path/to/escal && python scripts/run_alert_checks.py

On Windows (this project's dev machine), use Task Scheduler with the action
`python C:\\path\\to\\Ross\\scripts\\run_alert_checks.py`, trigger daily.

Requires the same environment the backend container runs with (DATABASE_URL,
SMTP_* — see backend/.env.example), since it talks to the DB and sends
email directly rather than through the API.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.services.alerts import run_all_alert_checks  # noqa: E402


def main():
    configure_logging(settings.log_level)
    run_all_alert_checks()


if __name__ == "__main__":
    main()
