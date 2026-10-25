import asyncio
import json
import time

import httpx
import pytest
from pydantic import SecretStr

from app import scaleway
from app.config import settings
from app.model_errors import ModelError, ModelUnavailable
from app.scaleway import converse


def _chat_response(content) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        },
    )


async def _call() -> str:
    return await converse("system prompt", "user text", max_tokens=100, temperature=0)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "scaleway_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "scaleway_model", "test-model")


class FakeScaleway:
    """Stands in for the network: records requests, answers with `respond`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.respond = lambda request: _chat_response("answer")

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def scaleway_api(configured, monkeypatch):
    """Opt in to outbound HTTP, answered by a FakeScaleway instead of the network."""
    fake = FakeScaleway()

    async def handle(transport, request):
        fake.requests.append(request)
        result = fake.respond(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return fake


@pytest.mark.asyncio
async def test_converse_sends_one_user_message_with_the_system_prompt(scaleway_api):
    assert await _call() == "answer"

    request = scaleway_api.requests[0]
    assert str(request.url) == scaleway.CHAT_URL
    assert request.headers["Authorization"] == "Bearer test-key"
    # Structured outputs would enforce the format at the provider; callers ask
    # for it in the prompt instead and validate the answer themselves.
    assert scaleway_api.payload() == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user text"},
        ],
        "max_tokens": 100,
        "temperature": 0,
    }


@pytest.mark.asyncio
async def test_converse_strips_whitespace_from_the_settings(scaleway_api, monkeypatch):
    monkeypatch.setattr(settings, "scaleway_api_key", SecretStr("  test-key\n"))
    monkeypatch.setattr(settings, "scaleway_model", " other-model ")

    await _call()

    assert scaleway_api.requests[0].headers["Authorization"] == "Bearer test-key"
    assert scaleway_api.payload()["model"] == "other-model"


@pytest.mark.asyncio
async def test_null_content_returns_empty_text(scaleway_api):
    scaleway_api.respond = lambda request: _chat_response(None)

    assert await _call() == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json=["unexpected"]),
    ],
)
async def test_unexpected_answer_shape_raises_model_error(scaleway_api, response):
    scaleway_api.respond = lambda request: response

    with pytest.raises(ModelError, match="unexpected shape"):
        await _call()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_error_status_raises_model_error(scaleway_api, status_code):
    scaleway_api.respond = lambda request: httpx.Response(status_code, json={})

    with pytest.raises(ModelError, match=f"HTTP {status_code}") as caught:
        await _call()

    assert not isinstance(caught.value, ModelUnavailable)


@pytest.mark.asyncio
async def test_connection_failure_raises_model_error(scaleway_api):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    scaleway_api.respond = refuse

    with pytest.raises(ModelError, match="Model call failed"):
        await _call()


@pytest.mark.asyncio
async def test_non_ascii_key_raises_model_error(scaleway_api, monkeypatch):
    monkeypatch.setattr(settings, "scaleway_api_key", SecretStr("kéy"))

    with pytest.raises(ModelError, match="Model call failed"):
        await _call()

    assert scaleway_api.requests == []


@pytest.mark.asyncio
async def test_slow_model_call_times_out(scaleway_api, monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.5)
        return _chat_response("late")

    scaleway_api.respond = slow
    monkeypatch.setattr(scaleway, "CALL_TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    with pytest.raises(ModelError, match="timed out"):
        await _call()

    assert time.monotonic() - started < 0.4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_key", "model"),
    [
        (None, None),
        (SecretStr(""), "test-model"),
        (SecretStr("  "), "test-model"),
        (SecretStr("key"), None),
        (SecretStr("key"), " "),
    ],
)
async def test_missing_or_blank_settings_raise_unavailable(monkeypatch, api_key, model):
    monkeypatch.setattr(settings, "scaleway_api_key", api_key)
    monkeypatch.setattr(settings, "scaleway_model", model)

    # The autouse outbound-HTTP guard fails any request, so reaching the network
    # would show up as an AssertionError rather than ModelUnavailable.
    with pytest.raises(ModelUnavailable):
        await _call()


@pytest.mark.asyncio
async def test_real_outbound_request_is_blocked_in_tests(configured):
    with pytest.raises(AssertionError, match="real outbound HTTP"):
        await _call()
