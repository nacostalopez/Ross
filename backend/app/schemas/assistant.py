from typing import Literal, Optional

from pydantic import BaseModel


class AssistantEventIn(BaseModel):
    # `acted` is reserved until the assistant screen exists (stage 4.3), so it is not accepted here.
    kind: Literal["shown", "useful", "not_useful", "dismissed"]


class UtilityScoreOut(BaseModel):
    tool: str
    tier: str
    useful: int
    not_useful: int
    samples: int
    runs: int
    validator_rejection_rate: Optional[float]
    # None until there are enough verdicts to say anything (status "sin_datos").
    score: Optional[float]
    status: Literal["ok", "sin_datos"]
