"""Shared connector-status upsert helper.

Lives here (not in app/routes/connectors.py, where it originated) so both
the route layer and app/services/capi.py's background tasks can call it
without a routes -> services -> routes import cycle.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ConnectorStatus


def _upsert_connector_status(
    db: Session,
    store_id: UUID,
    provider: str,
    *,
    synced: bool = False,
    success: bool = False,
    error: Optional[str] = None,
) -> None:
    """Record connector health in connector_status (upsert by store_id+provider).

    Only the columns implied by the call are touched — a plain OAuth connect
    (synced=False) doesn't overwrite the last data-sync timestamp or outcome.
    """
    now = datetime.now(timezone.utc)
    values = {"store_id": store_id, "provider": provider, "last_error": error}
    if synced:
        values["last_synced_at"] = now
        values["last_sync_status"] = "success" if success else "error"
        values["last_sync_error"] = error
    if success:
        values["last_success_at"] = now

    stmt = pg_insert(ConnectorStatus.__table__).values(**values)
    update_cols = {k: stmt.excluded[k] for k in values if k not in ("store_id", "provider")}
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_id", "provider"],
        set_=update_cols,
    )
    db.execute(stmt)
    db.commit()
