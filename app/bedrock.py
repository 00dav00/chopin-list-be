"""Sends one prompt to a language model on Bedrock and returns its text answer.

The only module that talks to Bedrock. It knows nothing about shopping items.
"""

import asyncio
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import settings

logger = logging.getLogger(__name__)

# Tuning knobs are constants, not env vars (team convention #5). AWS lists this
# model's end of life as no sooner than 2026-12-02; swapping it is a one-line change.
BEDROCK_MODEL_ID = "mistral.mistral-large-3-675b-instruct"
BEDROCK_REGION = "eu-west-2"
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 20
CALL_TIMEOUT_SECONDS = 25.0


class BedrockError(Exception):
    """The model could not be reached or the call failed."""


class BedrockUnavailable(BedrockError):
    """Bedrock credentials are not configured."""


_client = None


def _credentials() -> tuple[str, str] | None:
    key_id = (settings.aws_access_key_id or "").strip()
    secret = settings.aws_secret_access_key
    secret_value = secret.get_secret_value().strip() if secret else ""
    if not key_id or not secret_value:
        return None
    return key_id, secret_value


def _create_client(key_id: str, secret: str):
    # Explicit credentials so stray AWS_* variables in the environment can't win.
    return boto3.client(
        "bedrock-runtime",
        region_name=BEDROCK_REGION,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )


def _get_client():
    global _client
    credentials = _credentials()
    if credentials is None:
        raise BedrockUnavailable("Bedrock credentials are not configured.")
    if _client is None:
        _client = _create_client(*credentials)
    return _client


def _response_text(response: dict) -> str:
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    return "".join(block.get("text", "") for block in blocks)


async def converse(system: str, text: str, *, max_tokens: int, temperature: float) -> str:
    """The model's text answer to one user message, or BedrockError."""
    client = _get_client()
    request = {
        "modelId": BEDROCK_MODEL_ID,
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": text}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(client.converse, **request), CALL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        logger.warning("Bedrock call timed out after %ss", CALL_TIMEOUT_SECONDS)
        raise BedrockError("Model call timed out.") from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "unknown")
        logger.warning("Bedrock call failed: %s", code)
        raise BedrockError(f"Model call failed: {code}") from exc
    except BotoCoreError as exc:
        logger.warning("Bedrock call failed: %s", type(exc).__name__)
        raise BedrockError("Model call failed.") from exc
    return _response_text(response)
