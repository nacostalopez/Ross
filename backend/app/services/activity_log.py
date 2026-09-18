"""Personal "actividad reciente" feed for the Perfil page — see
AccountActivityLog's docstring for why this is deliberately narrow (a
handful of call sites below, not a generic audit-logging decorator/
middleware). Adding a new logged action means calling log_activity() at
that one call site and adding its label to ACTION_LABELS
(frontend/app.js) — nothing else needs to change.
"""

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import AccountActivityLog


def log_activity(db: Session, account_id: UUID, user_id: UUID, action: str, detail: str) -> None:
    db.add(
        AccountActivityLog(
            id=uuid4(),
            account_id=account_id,
            user_id=user_id,
            action=action,
            detail=detail,
        )
    )
    db.commit()
