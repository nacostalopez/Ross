"""The default writer: fixed sentences filled in with the figures. No model, no network."""

from typing import Callable, Optional

from app.services.narrative_writers.base import NarrativeRequest, Outcome, WriteResult


class TemplateNarrativeWriter:
    provider = "template"
    model: Optional[str] = None

    def __init__(self, render: Callable[[dict], str]):
        # Injected rather than imported: the sentences live in weekly_narrative.py, which imports this
        # package, so importing them back from here would be circular.
        self._render = render

    def write(self, request: NarrativeRequest) -> WriteResult:
        return WriteResult(Outcome.WRITTEN, self._render(request.facts), provider=self.provider, model=self.model)
