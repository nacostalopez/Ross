"""Runs a writer and decides what comes out: the model's text if it can be trusted, the template's
otherwise, and whether the day's result is worth caching."""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from app.services.narrative_writers.base import NarrativeRequest, NarrativeWriter, Outcome
from app.services.narrative_writers.template_writer import TemplateNarrativeWriter

logger = logging.getLogger("ross.narrative")


@dataclass(frozen=True)
class NarrativeResult:
    text: str
    # "ia" when a model wrote it, "plantilla" when the template did.
    source: str
    provider: str
    model: Optional[str]
    # False when a model was supposed to write it and couldn't: the next visit should try again
    # instead of keeping the template for the whole day.
    cacheable: bool
    # Why a model's text wasn't used. For the logs and tests.
    reason: Optional[str] = None


class NarrativeService:
    def __init__(
        self,
        writer: Optional[NarrativeWriter],
        template: TemplateNarrativeWriter,
        figures_ok: Callable[[str, list[str]], bool],
    ):
        self._writer = writer
        self._template = template
        self._figures_ok = figures_ok

    def _from_template(
        self, request: NarrativeRequest, *, cacheable: bool, reason: Optional[str] = None
    ) -> NarrativeResult:
        written = self._template.write(request)
        return NarrativeResult(
            text=written.text or "",
            source="plantilla",
            provider=self._template.provider,
            model=None,
            cacheable=cacheable,
            reason=reason,
        )

    def generate(self, request: NarrativeRequest, *, use_model: bool = True) -> NarrativeResult:
        # No model configured, or nothing to write about: the template is the answer, and a normal one.
        if self._writer is None or not use_model:
            return self._from_template(request, cacheable=True)

        try:
            result = self._writer.write(request)
        except Exception:
            # A writer should return a failure, not raise. If one does, the dashboard must still load.
            logger.exception("narrative_writer_crashed", extra={"provider": self._writer.provider})
            return self._from_template(request, cacheable=False, reason="unexpected_error")

        if result.outcome is Outcome.DISABLED:
            return self._from_template(request, cacheable=True, reason="disabled")
        text = (result.text or "").strip()
        if result.outcome is Outcome.FAILED or not text:
            return self._from_template(request, cacheable=False, reason=result.reason or "empty")
        if not self._figures_ok(text, request.lines):
            logger.warning(
                "narrative_rejected_unknown_figures", extra={"provider": result.provider, "model": result.model}
            )
            return self._from_template(request, cacheable=False, reason="rejected_figures")

        return NarrativeResult(text=text, source="ia", provider=result.provider, model=result.model, cacheable=True)
