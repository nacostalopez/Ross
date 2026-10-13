"""Who writes the weekly narrative, kept apart from what it says.

weekly_narrative.py works out the figures and owns the cache; this package decides who turns them
into a sentence (the fixed template, or a model) and what to do when that fails. The template is
the default and needs nothing configured: a model is only used when a provider is set up.
"""

from app.services.narrative_writers.base import NarrativeRequest, NarrativeWriter, Outcome, WriteResult
from app.services.narrative_writers.factory import build_writer, resolve_provider
from app.services.narrative_writers.service import NarrativeResult, NarrativeService
from app.services.narrative_writers.template_writer import TemplateNarrativeWriter

__all__ = [
    "NarrativeRequest",
    "NarrativeResult",
    "NarrativeService",
    "NarrativeWriter",
    "Outcome",
    "TemplateNarrativeWriter",
    "WriteResult",
    "build_writer",
    "resolve_provider",
]
