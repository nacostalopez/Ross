"""The assistant's memory: a trace per tool run, an event per thing a person did with the result, and the
utility score computed from them (docs/ia/fase-4-2-diseno.md).

A trace keeps the structure of a call, never its content: no figure, no free text and no parameter value
except the bounded ones in BOUNDED_PARAMS. Recording is observation, so it never raises and never
changes the answer.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dependencies import is_demo_user
from app.models import AssistantEvent, AssistantTrace, User

logger = logging.getLogger("ross.assistant")

# Parameters whose value is safe to keep because it is a bounded integer chosen from a fixed range.
BOUNDED_PARAMS = frozenset({"days"})

# `acted` is reserved: nothing can be observed until the assistant screen exists (stage 4.3).
EVENT_KINDS = frozenset({"shown", "useful", "not_useful", "dismissed"})
RESERVED_KINDS = frozenset({"acted"})
# A person judges a result once: these replace each other.
_VERDICTS = ("useful", "not_useful", "dismissed")

SCORE_WINDOW_DAYS = 30
SCORE_MIN_SAMPLES = 20
RETENTION_DAYS = 180


def param_shape(params: Optional[dict]) -> dict:
    """The parameters a trace may keep: bounded integers by name. Everything else is dropped."""
    shape = {}
    for name, value in (params or {}).items():
        if name in BOUNDED_PARAMS and isinstance(value, int) and not isinstance(value, bool):
            shape[name] = value
    return shape


def record_trace(
    db: Session,
    *,
    user: User,
    store_id: Optional[UUID],
    tool: str,
    outcome: str,
    latency_ms: int,
    role: str,
    params: Optional[dict] = None,
    validator_rejected: bool = False,
    tier: str = "L0",
    provider: str = "template",
) -> Optional[UUID]:
    """Save one trace and return its id, or None if it wasn't saved. Never raises: a failure to record
    is logged and the answer goes out as it was. The demo account leaves no trace."""
    if is_demo_user(user):
        return None
    try:
        trace = AssistantTrace(
            id=uuid.uuid4(),
            account_id=user.account_id,
            store_id=store_id,
            tool=tool[:60],
            tier=tier,
            provider=provider,
            outcome=outcome,
            validator_rejected=validator_rejected,
            param_shape=param_shape(params),
            latency_ms=max(0, int(latency_ms)),
            role=role,
        )
        db.add(trace)
        db.commit()
        return trace.id
    except Exception:
        db.rollback()
        logger.warning("trace_not_saved", extra={"tool": tool}, exc_info=True)
        return None


def record_event(db: Session, *, user: User, trace_id: UUID, kind: str) -> Optional[AssistantEvent]:
    """Record what `user` did with a trace of their own account. Returns None if there is no such trace
    in their account (the same answer whether it doesn't exist or belongs to someone else) or they are
    the demo user. Raises ValueError for a kind that isn't allowed."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"Evento no permitido: {kind}")
    if is_demo_user(user):
        return None
    trace = db.query(AssistantTrace).filter_by(id=trace_id, account_id=user.account_id).first()
    if trace is None:
        return None

    # Two requests for the same mark can race: the second insert hits the unique key. The mark is
    # already there, so one retry finds it and just refreshes it.
    for attempt in (1, 2):
        try:
            return _save_event(db, user, trace, kind)
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise


def _save_event(db: Session, user: User, trace: AssistantTrace, kind: str) -> AssistantEvent:
    if kind in _VERDICTS:
        (
            db.query(AssistantEvent)
            .filter(
                AssistantEvent.trace_id == trace.id,
                AssistantEvent.user_id == user.id,
                AssistantEvent.kind.in_(_VERDICTS),
                AssistantEvent.kind != kind,
            )
            .delete(synchronize_session=False)
        )
    now = datetime.now(timezone.utc)
    event = db.query(AssistantEvent).filter_by(trace_id=trace.id, user_id=user.id, kind=kind).first()
    if event is None:
        event = AssistantEvent(id=uuid.uuid4(), trace_id=trace.id, account_id=user.account_id, user_id=user.id, kind=kind)
        db.add(event)
    else:
        # Saying it again is the latest judgement: the score window counts from now, not from the first click.
        event.created_at = now
    db.commit()
    return event


@dataclass(frozen=True)
class UtilityScore:
    tool: str
    tier: str
    useful: int
    not_useful: int
    runs: int
    validator_rejection_rate: Optional[float]
    # None until there are SCORE_MIN_SAMPLES verdicts: a percentage over three samples misleads.
    score: Optional[float]

    @property
    def samples(self) -> int:
        return self.useful + self.not_useful

    @property
    def status(self) -> str:
        return "ok" if self.score is not None else "sin_datos"


def utility_score(
    db: Session, *, account_id: UUID, tool: str, tier: str = "L0", now: Optional[datetime] = None
) -> UtilityScore:
    """useful / (useful + not useful) over the last 30 days, for one tool and tier of one account."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=SCORE_WINDOW_DAYS)
    verdicts = dict(
        db.query(AssistantEvent.kind, func.count(AssistantEvent.id))
        .join(AssistantTrace, AssistantTrace.id == AssistantEvent.trace_id)
        .filter(
            AssistantTrace.account_id == account_id,
            AssistantEvent.account_id == account_id,
            AssistantTrace.tool == tool,
            AssistantTrace.tier == tier,
            AssistantEvent.kind.in_(("useful", "not_useful")),
            AssistantEvent.created_at >= since,
        )
        .group_by(AssistantEvent.kind)
        .all()
    )
    runs, rejected = (
        db.query(func.count(AssistantTrace.id), func.count(AssistantTrace.id).filter(AssistantTrace.validator_rejected))
        .filter(
            AssistantTrace.account_id == account_id,
            AssistantTrace.tool == tool,
            AssistantTrace.tier == tier,
            AssistantTrace.created_at >= since,
        )
        .one()
    )
    useful, not_useful = verdicts.get("useful", 0), verdicts.get("not_useful", 0)
    samples = useful + not_useful
    return UtilityScore(
        tool=tool,
        tier=tier,
        useful=useful,
        not_useful=not_useful,
        runs=runs,
        validator_rejection_rate=(rejected / runs) if runs else None,
        score=(useful / samples) if samples >= SCORE_MIN_SAMPLES else None,
    )


def purge_expired(db: Session, now: Optional[datetime] = None) -> dict[str, int]:
    """Delete traces and events older than the retention period."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    events = db.query(AssistantEvent).filter(AssistantEvent.created_at < cutoff).delete(synchronize_session=False)
    traces = db.query(AssistantTrace).filter(AssistantTrace.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return {"events": events, "traces": traces}
