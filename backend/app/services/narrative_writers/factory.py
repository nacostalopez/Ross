"""Picks the writer from the settings.

Only the fixed template exists today: the weekly narrative was never written by a model in
production, and the experiment to see whether one would be worth it was set aside (docs/ia/fase-2-validador.md).
This is where a provider would be added: write a NarrativeWriter, list its name in PROVIDERS and return it from
build_writer(). The service, the figure check and the cache already handle a writer that fails or invents numbers.
"""

from typing import Optional

from app.services.narrative_writers.base import NarrativeWriter

PROVIDERS = ("template",)


def resolve_provider(narrative_provider: str) -> str:
    """The provider in effect: the explicit NARRATIVE_PROVIDER, or the template."""
    return (narrative_provider or "").strip().lower() or "template"


def check_provider(narrative_provider: str) -> None:
    """Raise on a configuration mistake. Called when the settings load, so a typo stops the app from
    starting instead of showing up later as a quiet fallback to the template."""
    provider = resolve_provider(narrative_provider)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown NARRATIVE_PROVIDER {provider!r}; use one of: {', '.join(PROVIDERS)}")


def build_writer(settings) -> Optional[NarrativeWriter]:
    """The model writer to use, or None to use the template alone."""
    check_provider(settings.narrative_provider)
    return None
