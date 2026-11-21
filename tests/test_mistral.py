import asyncio
import logging
import time

import httpx
import pytest
from pydantic import SecretStr

from app import mistral
from app.config import settings
from app.mistral import CHAT_COMPLETIONS_URL, converse
from app.model_errors import ModelError, ModelUnavailable

KEY = "mistral-key-sentinel-0123456789"


async def _call(text: str = "user text") -> str:
    return await converse("system prompt", text, max_tokens=100, temperature=0)


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", SecretStr(KEY))


def _answer(content) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
async def test_converse_sends_system_and_user_messages_with_the_bearer_key(api_key, mistral_api):
    assert await _call() == "answer"

    [request] = mistral_api.requests
    assert request.method == "POST"
    assert str(request.url) == CHAT_COMPLETIONS_URL
    assert request.headers["authorization"] == f"Bearer {KEY}"
    # The answer format is asked for in the prompt, so no response_format or
    # tools here: the providers then behave the same.
    assert mistral_api.payload() == {
        "model": settings.mistral_model,
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user text"},
        ],
        "max_tokens": 100,
        "temperature": 0,
    }


@pytest.mark.asyncio
async def test_converse_uses_the_configured_model(api_key, mistral_api, monkeypatch):
    monkeypatch.setattr(settings, "mistral_model", "some-other-model")

    await _call()

    assert mistral_api.payload()["model"] == "some-other-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, SecretStr(""), SecretStr("   ")])
async def test_missing_or_blank_key_is_unavailable_without_a_request(
    monkeypatch, mistral_api, caplog, secret
):
    monkeypatch.setattr(settings, "mistral_api_key", secret)

    with caplog.at_level(logging.ERROR, logger="app.mistral"):
        with pytest.raises(ModelUnavailable):
            await _call()

    assert mistral_api.requests == []
    assert "MISTRAL_API_KEY is not set" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_key", ["key with-nbsp", "key“quoted”", "key\x00nul", "two words"])
async def test_malformed_key_is_unavailable_and_never_logged(monkeypatch, mistral_api, caplog, bad_key):
    monkeypatch.setattr(settings, "mistral_api_key", SecretStr(bad_key))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelUnavailable):
            await _call()

    assert mistral_api.requests == []
    assert "MISTRAL_API_KEY" in caplog.text
    assert bad_key not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_error_status_raises_model_error_with_the_status(api_key, mistral_api, caplog, status):
    mistral_api.respond = lambda request: httpx.Response(
        status, json={"object": "error", "message": "nope", "type": "some_error_type"}
    )

    with caplog.at_level(logging.WARNING, logger="app.mistral"):
        with pytest.raises(ModelError, match=str(status)):
            await _call()

    assert f"HTTP {status}" in caplog.text
    assert "'some_error_type'" in caplog.text
    assert KEY not in caplog.text


@pytest.mark.asyncio
async def test_error_message_text_is_never_logged(api_key, mistral_api, caplog):
    # Validation errors can echo the request, which holds shoppers' item names.
    mistral_api.respond = lambda request: httpx.Response(
        422, json={"message": "bad input near 'secret-item-name'", "detail": "secret-item-name"}
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelError):
            await _call("secret-item-name")

    assert "HTTP 422" in caplog.text
    assert "secret-item-name" not in caplog.text


@pytest.mark.asyncio
async def test_non_json_error_body_logs_its_type_and_size_only(api_key, mistral_api, caplog):
    mistral_api.respond = lambda request: httpx.Response(
        502, text="<html>proxy echo: Bearer xyz</html>", headers={"content-type": "text/html"}
    )

    with caplog.at_level(logging.WARNING, logger="app.mistral"):
        with pytest.raises(ModelError):
            await _call()

    assert "text/html" in caplog.text
    assert "proxy echo" not in caplog.text


@pytest.mark.asyncio
async def test_transport_error_raises_model_error(api_key, mistral_api):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    mistral_api.respond = refuse

    with pytest.raises(ModelError):
        await _call()


@pytest.mark.asyncio
async def test_slow_answer_times_out(api_key, mistral_api, monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.5)
        return _answer("late")

    mistral_api.respond = slow
    monkeypatch.setattr(mistral, "CALL_TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    with pytest.raises(ModelError, match="timed out"):
        await _call()

    assert time.monotonic() - started < 0.4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"id": "x"}),
        httpx.Response(200, json={"choices": []}),
        _answer(None),
        _answer([]),
        _answer([{"type": "thinking", "thinking": []}]),
    ],
)
async def test_unusable_answer_raises_model_error(api_key, mistral_api, response):
    mistral_api.respond = lambda request: response

    with pytest.raises(ModelError):
        await _call()


@pytest.mark.asyncio
async def test_real_network_is_blocked_without_the_fake(api_key):
    # The autouse outbound-HTTP guard refuses the request; it surfaces here as
    # an AssertionError rather than reaching api.mistral.ai.
    with pytest.raises(AssertionError, match="real outbound HTTP"):
        await _call()


@pytest.mark.asyncio
async def test_client_ignores_proxy_env_and_redirects(api_key, mistral_api, monkeypatch):
    built: list[dict] = []
    real_client = httpx.AsyncClient

    def recording_client(**kwargs):
        built.append(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(mistral.httpx, "AsyncClient", recording_client)

    await _call()

    [kwargs] = built
    assert kwargs["trust_env"] is False
    assert kwargs["follow_redirects"] is False
    assert kwargs["verify"] is mistral.SSL_CONTEXT
    assert kwargs["timeout"].connect == mistral.CONNECT_TIMEOUT_SECONDS
    assert kwargs["timeout"].read == mistral.READ_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_chunked_answer_keeps_only_the_text_chunks(api_key, mistral_api):
    mistral_api.respond = lambda request: _answer(
        [
            {"type": "thinking", "thinking": [{"type": "text", "text": "hidden"}]},
            {"type": "text", "text": '{"a":'},
            {"type": "text", "text": " 1}"},
        ]
    )

    assert await _call() == '{"a": 1}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "logged", "not_logged"),
    [
        ({"code": "3051"}, "code '3051'", None),
        ({"type": "has spaces and 'quotes'"}, "application/json", "has spaces"),
    ],
)
async def test_error_detail_logs_only_a_strict_code(
    api_key, mistral_api, caplog, body, logged, not_logged
):
    mistral_api.respond = lambda request: httpx.Response(400, json=body)

    with caplog.at_level(logging.WARNING, logger="app.mistral"):
        with pytest.raises(ModelError):
            await _call()

    assert logged in caplog.text
    if not_logged:
        assert not_logged not in caplog.text
