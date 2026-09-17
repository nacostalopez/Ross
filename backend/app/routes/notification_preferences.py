from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import NotificationChannelPreference, User
from app.schemas.notification_preferences import (
    NotificationChannelPreferenceItem,
    NotificationPreferencesIn,
    NotificationPreferencesOut,
)

router = APIRouter(prefix="/notification-preferences", tags=["notifications"])

# Every event type a user can have an opinion about — see
# app/services/notifications.py::send_to_store, the only place these are
# actually read.
EVENT_TYPES = ["cac_alert", "roas_alert", "weekly_report"]


def _current_preferences(db: Session, user: User) -> NotificationPreferencesOut:
    rows = {
        row.event_type: row
        for row in db.query(NotificationChannelPreference).filter_by(user_id=user.id).all()
    }
    return NotificationPreferencesOut(
        preferences=[
            NotificationChannelPreferenceItem(
                event_type=event_type,
                email_enabled=rows[event_type].email_enabled if event_type in rows else True,
                push_enabled=rows[event_type].push_enabled if event_type in rows else True,
            )
            for event_type in EVENT_TYPES
        ]
    )


@router.get("", response_model=NotificationPreferencesOut)
def get_notification_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """No saved row for an event type means both channels are on — this is
    personal to the account (not gated by store role), same as the push
    subscription toggle itself."""
    return _current_preferences(db, user)


@router.put("", response_model=NotificationPreferencesOut)
def set_notification_preferences(
    payload: NotificationPreferencesIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for item in payload.preferences:
        row = db.get(NotificationChannelPreference, (user.id, item.event_type))
        if row:
            row.email_enabled = item.email_enabled
            row.push_enabled = item.push_enabled
        else:
            db.add(
                NotificationChannelPreference(
                    user_id=user.id,
                    event_type=item.event_type,
                    email_enabled=item.email_enabled,
                    push_enabled=item.push_enabled,
                )
            )
    db.commit()
    return _current_preferences(db, user)
