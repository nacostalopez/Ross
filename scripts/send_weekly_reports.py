#!/usr/bin/env python
"""Send the weekly summary email to every store that opted in.

No in-process scheduler in the FastAPI app (see
app/services/reports.py's module docstring) — meant to be invoked by a
real cron instead:

    # crontab -e, every Monday at 09:00
    0 9 * * 1 cd /path/to/ross && python scripts/send_weekly_reports.py

On Windows (this project's dev machine), use Task Scheduler with the action
`python C:\\path\\to\\Ross\\scripts\\send_weekly_reports.py`, trigger weekly.

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
from app.services.reports import run_all_weekly_reports  # noqa: E402


def main():
    configure_logging(settings.log_level)
    run_all_weekly_reports()


if __name__ == "__main__":
    main()
