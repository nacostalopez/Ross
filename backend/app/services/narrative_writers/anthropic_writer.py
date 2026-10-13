"""Claude as a narrative writer. Only imported when this provider is selected, so the `anthropic`
package isn't needed (or even installed) for the template to work."""

import logging

import anthropic

from app.services.narrative_writers.base import NarrativeRequest, Outcome, WriteResult

logger = logging.getLogger("ross.narrative")


class AnthropicNarrativeWriter:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 30.0):
        self.model = model
        self._api_key = api_key
        self._timeout = timeout

    def _failed(self, reason: str) -> WriteResult:
        return WriteResult(Outcome.FAILED, provider=self.provider, model=self.model, reason=reason)

    def write(self, request: NarrativeRequest) -> WriteResult:
        client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout, max_retries=1)
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                output_config={"effort": "low"},
                # A refused request is retried on the fallback model Anthropic recommends.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=request.system_prompt,
                messages=[{"role": "user", "content": "Datos de la tienda:\n" + "\n".join(request.lines)}],
            )
        except anthropic.RateLimitError:
            logger.warning("narrative_rate_limited")
            return self._failed("rate_limited")
        except anthropic.APIStatusError as exc:
            logger.warning("narrative_api_error", extra={"status_code": exc.status_code})
            return self._failed("api_error")
        except anthropic.APIConnectionError:
            logger.warning("narrative_connection_error")
            return self._failed("connection_error")
        except anthropic.APIError:
            # Anything else the library raises (a response it couldn't parse, for one): never let it
            # reach the request.
            logger.warning("narrative_provider_error")
            return self._failed("provider_error")

        if response.stop_reason != "end_turn":
            logger.warning("narrative_not_finished", extra={"stop_reason": response.stop_reason})
            return self._failed("not_finished")
        text = " ".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            return self._failed("empty")
        return WriteResult(Outcome.WRITTEN, text, provider=self.provider, model=self.model)
