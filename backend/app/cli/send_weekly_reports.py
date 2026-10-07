"""Send the weekly summary email to every store that opted in.

There's no in-process scheduler in the FastAPI app (see
app/services/reports.py's module docstring); a cron runs this every Monday
instead. In production it's the Railway cron service "weekly-reports" (see
DEPLOY.md); locally:

    docker compose exec backend python -m app.cli.send_weekly_reports

It needs the backend's environment (DATABASE_URL, SMTP_*), since it talks to
the DB and sends email directly rather than through the API.
"""

from app.config import settings
from app.logging_config import configure_logging
from app.services.reports import run_all_weekly_reports


def main() -> None:
    configure_logging(settings.log_level)
    run_all_weekly_reports()


if __name__ == "__main__":
    main()
