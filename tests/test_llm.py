import pytest

from app import bedrock, llm, scaleway
from app.config import settings
from app.model_errors import ModelError, ModelUnavailable


class FakeProvider:
    """Stands in for one provider's converse: records calls, answers with its name."""

    def __init__(self, name):
        self.name = name
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    async def converse(self, system, text, *, max_tokens, temperature):
        self.calls.append((system, text, max_tokens, temperature))
        if self.error:
            raise self.error
        return f"{self.name} answer"


@pytest.fixture
def providers(monkeypatch):
    fakes = {"bedrock": FakeProvider("bedrock"), "scaleway": FakeProvider("scaleway")}
    monkeypatch.setattr(bedrock, "converse", fakes["bedrock"].converse)
    monkeypatch.setattr(scaleway, "converse", fakes["scaleway"].converse)
    return fakes


async def _call() -> str:
    return await llm.converse("system prompt", "user text", max_tokens=100, temperature=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen", ["bedrock", "scaleway"])
async def test_converse_uses_only_the_chosen_provider(providers, monkeypatch, chosen):
    monkeypatch.setattr(settings, "model_provider", chosen)

    assert await _call() == f"{chosen} answer"

    assert providers[chosen].calls == [("system prompt", "user text", 100, 0)]
    other = next(name for name in providers if name != chosen)
    assert providers[other].calls == []


@pytest.mark.asyncio
async def test_provider_name_ignores_case_and_whitespace(providers, monkeypatch):
    monkeypatch.setattr(settings, "model_provider", " Scaleway\n")

    assert await _call() == "scaleway answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [None, "", "  ", "openai"])
async def test_missing_or_unknown_provider_raises_unavailable(providers, monkeypatch, provider):
    monkeypatch.setattr(settings, "model_provider", provider)

    with pytest.raises(ModelUnavailable, match="bedrock, scaleway"):
        await _call()

    assert all(fake.calls == [] for fake in providers.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ModelError("call failed"), ModelUnavailable("no key")])
async def test_provider_errors_reach_the_caller_unchanged(providers, monkeypatch, error):
    monkeypatch.setattr(settings, "model_provider", "scaleway")
    providers["scaleway"].error = error

    with pytest.raises(type(error)) as caught:
        await _call()

    assert caught.value is error
