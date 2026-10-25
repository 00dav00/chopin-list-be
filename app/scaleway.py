"""Sends one prompt to a language model on Scaleway and returns its text answer.

The only module that talks to the model provider. It knows nothing about shopping items.
"""

import asyncio
import logging

import httpx

from .config import settings
from .model_errors import ModelError, ModelUnavailable

logger = logging.getLogger(__name__)

# Where the API key is sent, so never configurable from env or request data.
# Scaleway serves it from Paris, which keeps item names in the EU.
CHAT_URL = "https://api.scaleway.ai/v1/chat/completions"
# Timeouts stay constants (team convention #5). httpx timeouts are per phase,
# so the whole call is also wrapped in CALL_TIMEOUT_SECONDS.
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 25.0
# Built once: each new client would otherwise re-parse the CA bundle,
# synchronously, on the event loop.
SSL_CONTEXT = httpx.create_ssl_context()


def _configured() -> tuple[str, str] | None:
    secret = settings.scaleway_api_key
    key = secret.get_secret_value().strip() if secret else ""
    model = (settings.scaleway_model or "").strip()
    if not key or not model:
        return None
    return key, model


def _response_text(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, LookupError, TypeError) as exc:
        raise ModelError("Model answer has an unexpected shape.") from exc
    return content if isinstance(content, str) else ""


async def converse(system: str, text: str, *, max_tokens: int, temperature: float) -> str:
    """The model's text answer to one user message, or ModelError."""
    configured = _configured()
    if configured is None:
        raise ModelUnavailable("SCALEWAY_API_KEY or SCALEWAY_MODEL is not configured.")
    api_key, model = configured
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        # Each setting is stated, not inherited: no redirects (they would
        # forward the Authorization header to the new host), no proxy env vars.
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                verify=SSL_CONTEXT,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            ) as client:
                response = await client.post(
                    CHAT_URL, headers={"Authorization": f"Bearer {api_key}"}, json=request
                )
    except (TimeoutError, httpx.TimeoutException) as exc:
        logger.warning("Model call timed out after %ss", CALL_TIMEOUT_SECONDS)
        raise ModelError("Model call timed out.") from exc
    # A non-ASCII key fails while the header is encoded, before any request.
    except (httpx.HTTPError, UnicodeEncodeError) as exc:
        logger.warning("Model call failed: %s", type(exc).__name__)
        raise ModelError("Model call failed.") from exc
    if not response.is_success:
        logger.warning("Model call failed: HTTP %s", response.status_code)
        raise ModelError(f"Model call failed: HTTP {response.status_code}")
    return _response_text(response)
