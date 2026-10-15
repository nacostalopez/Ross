"""The contract of an assistant tool (docs/ia/fase-4-1-diseno.md, section 5).

A tool is a fixed, typed way to read (and, later, change) something. The assistant never reaches the
database any other way, and never chooses which store, account or user a call is about: the server
resolves those from the signed-in user before any tool runs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Store, User

# Parameters a caller must never be able to set: who the call is about comes from the session.
IDENTITY_PARAMS = frozenset({"store_id", "account_id", "user_id"})


class Risk(str, Enum):
    READ = "read"
    # A change that is easy to undo. Runs only after the user confirmed it, and leaves a record.
    WRITE_LOW = "write_low"
    # Sending messages, deleting records, financial actions. Disabled until their own controls exist.
    HIGH = "high"


class DenialReason(str, Enum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_PARAMS = "invalid_params"
    MODULE_INACTIVE = "module_inactive"
    FORBIDDEN = "forbidden"
    PLAN_REQUIRED = "plan_required"
    CONFIRMATION_REQUIRED = "confirmation_required"
    HIGH_RISK_DISABLED = "high_risk_disabled"
    DEMO_READ_ONLY = "demo_read_only"


class ToolParams(BaseModel):
    """Base of every tool's parameters. Unknown fields are an error, so `store_id` can't ride along."""

    model_config = ConfigDict(extra="forbid")


class NoParams(ToolParams):
    pass


class PeriodParams(ToolParams):
    """The last `days` days, ending now: the same window the dashboard asks for."""

    days: int = Field(14, ge=1, description="Cantidad de días hacia atrás desde ahora")

    @field_validator("days")
    @classmethod
    def _within_the_window(cls, days: int) -> int:
        longest = settings.assistant_max_window_days
        if days > longest:
            raise ValueError(f"como máximo {longest} días")
        return days


@dataclass(frozen=True)
class ToolContext:
    """Who is asking and about what. Built by the server from the authenticated request, never by the caller."""

    db: Session
    user: User
    store: Store
    # The role this user holds on this store (a per-store override wins over the account role).
    role: str
    now: datetime
    # True only once the user confirmed this specific action in the interface.
    confirmed: bool = False

    def rolling_window(self, days: int) -> tuple[datetime, datetime]:
        return self.now - timedelta(days=days), self.now


@dataclass(frozen=True)
class Output:
    """What a tool's `run` returns: the data and, if it covers a stretch of time, which one."""

    data: Any
    window: Optional[tuple[datetime, datetime]] = None


@dataclass(frozen=True)
class Tool:
    name: str
    module: str
    # Read by the router and, if a model is plugged in, by the model to choose between tools.
    description: str
    params: type[ToolParams]
    # The pydantic type of the output (a model, or a list of them). The data is validated against it.
    result: Any
    run: Callable[[ToolContext, Any], Output]
    min_role: str = "viewer"
    plan_feature: Optional[str] = None
    risk: Risk = Risk.READ

    def __post_init__(self):
        leaked = IDENTITY_PARAMS & set(self.params.model_fields)
        if leaked:
            raise ValueError(f"Tool {self.name!r} takes {sorted(leaked)} as a parameter; identity comes from the session")


@dataclass(frozen=True)
class ToolResult:
    """The answer to one call: the data with where it came from, or a typed reason it wasn't given."""

    ok: bool
    tool: str
    # JSON-ready and validated against the tool's result type.
    data: Any = None
    # What the call was run with, after validation.
    params: Optional[dict] = None
    # When the data was read, and the stretch of time it covers. Together with `tool` and `params`
    # this is what lets the figure validator and the interface say where a number comes from.
    as_of: Optional[datetime] = None
    window: Optional[dict] = None
    currency: Optional[str] = None
    denial: Optional[DenialReason] = None
    message: Optional[str] = None
