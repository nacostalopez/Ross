"""Run the proactive CAC/ROAS alert check for every store that opted in.

There's no in-process scheduler in the FastAPI app (see
app/services/alerts.py's module docstring for why); a cron runs this daily
instead. In production it's the Railway cron service "alerts" (see
DEPLOY.md); locally:

    docker compose exec backend python -m app.cli.run_alert_checks

It needs the backend's environment (DATABASE_URL, SMTP_*), since it talks to
the DB and sends email directly rather than through the API.
"""

from app.config import settings
from app.logging_config import configure_logging
from app.services.alerts import run_all_alert_checks


def main() -> None:
    configure_logging(settings.log_level)
    run_all_alert_checks()


if __name__ == "__main__":
    main()
