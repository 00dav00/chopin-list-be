import time

import boto3
import pytest
from botocore.stub import ANY, Stubber
from pydantic import SecretStr

from app import bedrock
from app.bedrock import converse
from app.config import settings
from app.model_errors import ModelError, ModelUnavailable

REAL_CREATE_CLIENT = bedrock._create_client


def _converse_response(*texts: str) -> dict:
    return {
        "output": {
            "message": {"role": "assistant", "content": [{"text": text} for text in texts]}
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20},
        "metrics": {"latencyMs": 5},
    }


async def _call() -> str:
    return await converse("system prompt", "user text", max_tokens=100, temperature=0)


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(settings, "aws_access_key_id", "test-key-id")
    monkeypatch.setattr(settings, "aws_secret_access_key", SecretStr("test-secret"))


@pytest.fixture
def stubber(credentials, monkeypatch):
    """A real client built offline, wrapped in a Stubber; no network is touched."""
    client = boto3.client(
        "bedrock-runtime",
        region_name="eu-west-2",
        aws_access_key_id="test-key-id",
        aws_secret_access_key="test-secret",
    )
    monkeypatch.setattr(bedrock, "_client", client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_converse_sends_one_user_message_with_the_system_prompt(stubber):
    stubber.add_response(
        "converse",
        _converse_response("answer"),
        {
            "modelId": settings.bedrock_model_id,
            "system": [{"text": "system prompt"}],
            "messages": [{"role": "user", "content": [{"text": "user text"}]}],
            # Structured outputs compile a schema on first use (up to minutes) and
            # would blow the request limit after a quiet day, so callers ask for a
            # format in the prompt instead: no outputConfig or toolConfig here.
            "inferenceConfig": {"maxTokens": 100, "temperature": 0},
        },
    )

    assert await _call() == "answer"


@pytest.mark.asyncio
async def test_converse_uses_the_configured_model(stubber, monkeypatch):
    monkeypatch.setattr(settings, "bedrock_model_id", "some.other-model")
    stubber.add_response(
        "converse",
        _converse_response("answer"),
        {"modelId": "some.other-model", "system": ANY, "messages": ANY, "inferenceConfig": ANY},
    )

    assert await _call() == "answer"


@pytest.mark.asyncio
async def test_converse_joins_text_blocks(stubber):
    stubber.add_response("converse", _converse_response('{"a":', " 1}"))

    assert await _call() == '{"a": 1}'


@pytest.mark.asyncio
async def test_aws_error_raises_model_error(stubber):
    stubber.add_client_error("converse", service_error_code="ThrottlingException")

    with pytest.raises(ModelError, match="ThrottlingException"):
        await _call()


@pytest.mark.asyncio
async def test_slow_model_call_times_out(credentials, monkeypatch):
    class SlowClient:
        def converse(self, **kwargs):
            time.sleep(0.5)
            return _converse_response("late")

    monkeypatch.setattr(bedrock, "_client", SlowClient())
    monkeypatch.setattr(bedrock, "CALL_TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    with pytest.raises(ModelError):
        await _call()

    assert time.monotonic() - started < 0.4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key_id", "secret"),
    [(None, None), ("", SecretStr("")), ("  ", SecretStr("secret")), ("key", None)],
)
async def test_missing_or_blank_credentials_raise_unavailable(monkeypatch, key_id, secret):
    monkeypatch.setattr(settings, "aws_access_key_id", key_id)
    monkeypatch.setattr(settings, "aws_secret_access_key", secret)

    # The autouse guard fails if a client is created, so reaching it would show
    # up as an AssertionError rather than ModelUnavailable.
    with pytest.raises(ModelUnavailable):
        await _call()


@pytest.mark.asyncio
async def test_real_client_creation_is_blocked_in_tests(credentials):
    with pytest.raises(AssertionError, match="real Bedrock client"):
        await _call()


def test_create_client_uses_explicit_credentials_region_and_no_retries(monkeypatch):
    monkeypatch.setattr(settings, "bedrock_region", "eu-central-1")
    # Captured at import, before the autouse guard swaps the factory out.
    client = REAL_CREATE_CLIENT("explicit-key", "explicit-secret")

    assert client.meta.region_name == "eu-central-1"
    assert client.meta.config.connect_timeout == bedrock.CONNECT_TIMEOUT_SECONDS
    assert client.meta.config.read_timeout == bedrock.READ_TIMEOUT_SECONDS
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client._request_signer._credentials.access_key == "explicit-key"
