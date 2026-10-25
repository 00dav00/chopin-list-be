"""Sends one prompt to the model provider chosen by MODEL_PROVIDER.

Each provider module makes its own call and reads its own settings; this only
picks one. Callers get the text answer, or ModelError.
"""

from . import bedrock, scaleway
from .config import settings
from .model_errors import ModelUnavailable

# Looked up at call time, so tests can swap a provider's converse.
PROVIDERS = {"bedrock": bedrock, "scaleway": scaleway}


async def converse(system: str, text: str, *, max_tokens: int, temperature: float) -> str:
    """The chosen provider's text answer to one user message, or ModelError."""
    provider = PROVIDERS.get((settings.model_provider or "").strip().lower())
    if provider is None:
        raise ModelUnavailable(f"MODEL_PROVIDER must be one of: {', '.join(PROVIDERS)}.")
    return await provider.converse(
        system, text, max_tokens=max_tokens, temperature=temperature
    )
