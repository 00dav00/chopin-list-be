"""Sends one prompt to a language model on Mistral's own API and returns its text answer.

The only module that talks to api.mistral.ai. It knows nothing about shopping items.
"""

import asyncio
import json
import logging
import re

import httpx

from .config import settings
from .model_errors import ModelError, ModelUnavailable
from .utils import is_header_safe

logger = logging.getLogger(__name__)

# Where the API key is sent, so never configurable from env or request data.
CHAT_COMPLETIONS_URL = "https://api.mistral.ai/v1/chat/completions"
# Same budget as app/bedrock.py so either provider fits the endpoint's limit.
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 25.0
# Built once: each new client would otherwise re-parse the CA bundle,
# synchronously, on the event loop.
SSL_CONTEXT = httpx.create_ssl_context()
# Error messages can quote the request (shoppers' item names), so only a short
# error type or code is ever logged, never the message text.
ERROR_BODY_LIMIT = 4096
ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _api_key() -> str:
    secret = settings.mistral_api_key
    key = secret.get_secret_value().strip() if secret else ""
    if not key:
        logger.error("Mistral is unavailable: MISTRAL_API_KEY is not set.")
        raise ModelUnavailable("Mistral API key is not configured.")
    # Anything else fails while building the header (a 500, not a 503) or, for
    # a control character, gets the key quoted in a logged exception.
    if not is_header_safe(key):
        logger.error(
            "Mistral is unavailable: MISTRAL_API_KEY contains spaces, control or "
            "non-ASCII characters. Re-paste it; the value is not logged."
        )
        raise ModelUnavailable("Mistral API key is malformed.")
    return key


def _error_detail(response: httpx.Response) -> str:
    """Mistral's error type or code, or a description of a body that is not its JSON."""
    try:
        body = json.loads(response.content[:ERROR_BODY_LIMIT])
    except ValueError:
        body = None
    if isinstance(body, dict):
        for field in ("type", "code"):
            value = body.get(field)
            if isinstance(value, str) and ERROR_CODE.fullmatch(value):
                return f"{field} {value!r}"
    return "body: %s, %d bytes" % (
        response.headers.get("content-type", "no content type"),
        len(response.content),
    )


def _response_text(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ModelError("Mistral answer has no message content.") from exc
    if isinstance(content, str):
        return content
    # Some models answer in chunks (reasoning ones add "thinking" chunks); keep
    # only the text, as app/bedrock.py does with its content blocks.
    if isinstance(content, list):
        texts = [
            chunk["text"]
            for chunk in content
            if isinstance(chunk, dict)
            and chunk.get("type") == "text"
            and isinstance(chunk.get("text"), str)
        ]
        if texts:
            return "".join(texts)
    raise ModelError("Mistral answer content is not text.")


async def converse(system: str, text: str, *, max_tokens: int, temperature: float) -> str:
    """The model's text answer to one user message, or ModelError."""
    api_key = _api_key()
    payload = {
        "model": settings.mistral_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            # Each setting is stated, not inherited: no redirects (they would
            # forward the bearer token to the new host), no proxy env vars.
            async with httpx.AsyncClient(
                verify=SSL_CONTEXT,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            ) as client:
                response = await client.post(
                    CHAT_COMPLETIONS_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                    json=payload,
                )
    except TimeoutError as exc:
        logger.warning("Mistral call timed out after %ss", CALL_TIMEOUT_SECONDS)
        raise ModelError("Model call timed out.") from exc
    except httpx.HTTPError as exc:
        logger.warning("Mistral call failed: %s", type(exc).__name__)
        raise ModelError("Model call failed.") from exc

    if not response.is_success:
        status = response.status_code
        logger.warning("Mistral call failed: HTTP %s, %s", status, _error_detail(response))
        raise ModelError(f"Model call failed: HTTP {status}")
    return _response_text(response)
