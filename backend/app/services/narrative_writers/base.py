"""The contract every narrative writer follows."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol


@dataclass(frozen=True)
class NarrativeRequest:
    """Everything a writer may need. A model reads `lines` and `system_prompt`; the template reads
    `facts` (the same figures, before they were turned into prompt lines)."""

    facts: dict
    lines: list[str]
    system_prompt: str


class Outcome(str, Enum):
    WRITTEN = "written"
    # The writer isn't configured to run, which is a normal state and not an error.
    DISABLED = "disabled"
    # A timeout, a rate limit, an outage or an unusable response. Worth trying again later.
    FAILED = "failed"


@dataclass(frozen=True)
class WriteResult:
    outcome: Outcome
    text: Optional[str] = None
    provider: str = ""
    model: Optional[str] = None
    # Why it failed, in a word for the logs ("rate_limited", "timeout", ...). Never user-facing.
    reason: Optional[str] = None


class NarrativeWriter(Protocol):
    provider: str
    model: Optional[str]

    def write(self, request: NarrativeRequest) -> WriteResult:  # pragma: no cover - the contract
        ...
