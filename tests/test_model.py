import logging

import pytest
from pydantic import SecretStr

from app import bedrock, mistral, model
from app.config import settings
from app.model_errors import ModelError, ModelUnavailable


class FakeProvider:
    """Stands in for a provider's converse: records calls, answers with `answer`."""

    def __init__(self, answer="answer"):
        self.calls: list[tuple] = []
        self.answer = answer
        self.error: Exception | None = None

    async def converse(self, system, text, *, max_tokens, temperature):
        self.calls.append((system, text, max_tokens, temperature))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def fakes(monkeypatch):
    fake_bedrock, fake_mistral = FakeProvider("from bedrock"), FakeProvider("from mistral")
    monkeypatch.setattr(bedrock, "converse", fake_bedrock.converse)
    monkeypatch.setattr(mistral, "converse", fake_mistral.converse)
    return fake_bedrock, fake_mistral


async def _call() -> str:
    return await model.converse("system prompt", "user text", max_tokens=100, temperature=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["bedrock", " Bedrock ", "BEDROCK"])
async def test_bedrock_is_selected_and_receives_the_arguments(fakes, monkeypatch, value):
    fake_bedrock, fake_mistral = fakes
    monkeypatch.setattr(settings, "model_provider", value)

    assert await _call() == "from bedrock"
    assert fake_bedrock.calls == [("system prompt", "user text", 100, 0)]
    assert fake_mistral.calls == []


@pytest.mark.asyncio
async def test_mistral_is_selected(fakes, monkeypatch):
    fake_bedrock, fake_mistral = fakes
    monkeypatch.setattr(settings, "model_provider", "mistral")

    assert await _call() == "from mistral"
    assert fake_mistral.calls == [("system prompt", "user text", 100, 0)]
    assert fake_bedrock.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "   "])
async def test_absent_provider_is_unavailable_and_logged(fakes, monkeypatch, caplog, value):
    monkeypatch.setattr(settings, "model_provider", value)

    with caplog.at_level(logging.ERROR, logger="app.model"):
        with pytest.raises(ModelUnavailable):
            await _call()

    assert "MODEL_PROVIDER is not set" in caplog.text
    assert all(fake.calls == [] for fake in fakes)


@pytest.mark.asyncio
async def test_unknown_provider_is_unavailable_and_logged(fakes, monkeypatch, caplog):
    monkeypatch.setattr(settings, "model_provider", "openai")

    with caplog.at_level(logging.ERROR, logger="app.model"):
        with pytest.raises(ModelUnavailable):
            await _call()

    assert "'openai'" in caplog.text
    assert all(fake.calls == [] for fake in fakes)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ModelError("down"), ModelUnavailable("no key")])
async def test_provider_errors_reach_the_caller_unchanged(fakes, monkeypatch, error):
    fakes[0].error = error
    monkeypatch.setattr(settings, "model_provider", "bedrock")

    with pytest.raises(type(error)) as raised:
        await _call()

    assert raised.value is error


# --- each provider reads only its own credentials ---------------------------


@pytest.mark.asyncio
async def test_mistral_without_its_key_is_unavailable_even_with_aws_keys(monkeypatch, mistral_api):
    monkeypatch.setattr(settings, "model_provider", "mistral")
    monkeypatch.setattr(settings, "aws_access_key_id", "aws-key")
    monkeypatch.setattr(settings, "aws_secret_access_key", SecretStr("aws-secret"))
    monkeypatch.setattr(settings, "mistral_api_key", None)

    with pytest.raises(ModelUnavailable):
        await _call()

    assert mistral_api.requests == []


@pytest.mark.asyncio
async def test_bedrock_without_its_keys_is_unavailable_even_with_mistral_key(monkeypatch):
    monkeypatch.setattr(settings, "model_provider", "bedrock")
    monkeypatch.setattr(settings, "aws_access_key_id", None)
    monkeypatch.setattr(settings, "aws_secret_access_key", None)
    monkeypatch.setattr(settings, "mistral_api_key", SecretStr("mistral-key"))

    # The autouse guard fails if a Bedrock client is built, so reaching it would
    # show up as an AssertionError rather than ModelUnavailable.
    with pytest.raises(ModelUnavailable):
        await _call()
