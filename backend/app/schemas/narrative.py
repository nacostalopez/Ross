from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel


class NarrativeFact(BaseModel):
    label: str
    value: str
    # Against the previous 7 days; None when there's nothing meaningful to compare with.
    change_pct: Optional[float] = None


class NarrativeOut(BaseModel):
    text: str
    mood: Literal["festeja", "preocupada", "neutral"]
    # "ia" when Claude wrote the text, "plantilla" when it came from the fixed template.
    source: Literal["ia", "plantilla"]
    # The text covers the 7 full days before this date.
    period_end: date
    facts: list[NarrativeFact]
