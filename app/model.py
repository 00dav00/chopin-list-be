"""Sends one prompt to the language model chosen by MODEL_PROVIDER.

Callers stay unaware of which provider answers; each provider module checks its
own credentials.
"""

import logging

from . import bedrock, mistral
from .config import settings
from .model_errors import ModelUnavailable

logger = logging.getLogger(__name__)

PROVIDERS = {"bedrock": bedrock, "mistral": mistral}


def _provider():
    # Read on every call, like the Brevo-or-SMTP choice, so tests can patch it.
    name = (settings.model_provider or "").strip().lower()
    if not name:
        logger.error("Grouping is unavailable: MODEL_PROVIDER is not set.")
        raise ModelUnavailable("No model provider is configured.")
    provider = PROVIDERS.get(name)
    if provider is None:
        logger.error(
            "Grouping is unavailable: MODEL_PROVIDER=%r is not one of %s.",
            settings.model_provider,
            ", ".join(PROVIDERS),
        )
        raise ModelUnavailable("Unknown model provider.")
    return provider


async def converse(system: str, text: str, *, max_tokens: int, temperature: float) -> str:
    """The chosen model's text answer to one user message, or ModelError."""
    return await _provider().converse(
        system, text, max_tokens=max_tokens, temperature=temperature
    )
