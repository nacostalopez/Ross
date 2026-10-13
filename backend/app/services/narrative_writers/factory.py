"""Picks the writer from the settings."""

from typing import Optional

from app.services.narrative_writers.base import NarrativeWriter

PROVIDERS = ("template", "anthropic")


def resolve_provider(narrative_provider: str, anthropic_api_key: str) -> str:
    """The provider in effect. An explicit NARRATIVE_PROVIDER wins; without one, a loaded
    ANTHROPIC_API_KEY keeps meaning "use Claude", so existing setups behave as before."""
    explicit = (narrative_provider or "").strip().lower()
    if explicit:
        return explicit
    return "anthropic" if anthropic_api_key else "template"


def check_provider(narrative_provider: str, anthropic_api_key: str) -> None:
    """Raise on a configuration mistake. Called when the settings load, so a typo stops the app from
    starting instead of showing up later as a quiet fallback to the template."""
    provider = resolve_provider(narrative_provider, anthropic_api_key)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown NARRATIVE_PROVIDER {provider!r}; use one of: {', '.join(PROVIDERS)}")
    if provider == "anthropic" and not anthropic_api_key:
        raise ValueError("NARRATIVE_PROVIDER=anthropic needs ANTHROPIC_API_KEY")


def build_writer(settings) -> Optional[NarrativeWriter]:
    """The model writer to use, or None to use the template alone."""
    check_provider(settings.narrative_provider, settings.anthropic_api_key)
    provider = resolve_provider(settings.narrative_provider, settings.anthropic_api_key)
    if provider == "template":
        return None
    # Imported here so the `anthropic` package is only needed when this provider is selected.
    from app.services.narrative_writers.anthropic_writer import AnthropicNarrativeWriter

    return AnthropicNarrativeWriter(settings.anthropic_api_key, settings.narrative_model)
