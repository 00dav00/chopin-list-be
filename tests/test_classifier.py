import asyncio
import json
import time

import boto3
import pytest
from botocore.stub import ANY, Stubber
from pydantic import SecretStr

from app import classifier
from app.classifier import ClassifierError, ClassifierUnavailable, classify, clean_name
from app.config import settings

REAL_CREATE_CLIENT = classifier._create_client

GROUPS = ["Fruit", "Dairy"]


def _converse_response(text: str) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20},
        "metrics": {"latencyMs": 5},
    }


def _answer(*pairs) -> str:
    return json.dumps({"assignments": [{"i": i, "g": g} for i, g in pairs]})


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(settings, "aws_access_key_id", "test-key-id")
    monkeypatch.setattr(settings, "aws_secret_access_key", SecretStr("test-secret"))


@pytest.fixture
def bedrock(credentials, monkeypatch):
    """A real client built offline, wrapped in a Stubber; no network is touched."""
    client = boto3.client(
        "bedrock-runtime",
        region_name=classifier.BEDROCK_REGION,
        aws_access_key_id="test-key-id",
        aws_secret_access_key="test-secret",
    )
    monkeypatch.setattr(classifier, "_client", client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def _expect(bedrock, text: str, expected_params=None):
    bedrock.add_response(
        "converse",
        _converse_response(text),
        expected_params or {
            "modelId": classifier.BEDROCK_MODEL_ID,
            "system": ANY,
            "messages": ANY,
            "inferenceConfig": ANY,
        },
    )


@pytest.mark.asyncio
async def test_classify_maps_each_item_to_its_group(bedrock):
    _expect(bedrock, _answer((0, 1), (1, 0), (2, 2)))

    result = await classify(["milk", "apples", "birthday card"], GROUPS)

    assert result == [1, 0, None]


@pytest.mark.asyncio
async def test_classify_sends_numbered_items_and_groups_with_other_last(bedrock):
    captured = {}

    def capture(params, **kwargs):
        captured.update(params)

    bedrock.client.meta.events.register("provide-client-params.bedrock-runtime.Converse", capture)
    _expect(bedrock, _answer((0, 0)))

    await classify(["apples"], GROUPS)

    payload = json.loads(captured["messages"][0]["content"][0]["text"])
    assert payload == {
        "groups": [
            {"g": 0, "name": "Fruit"},
            {"g": 1, "name": "Dairy"},
            {"g": 2, "name": "Other"},
        ],
        "items": [{"i": 0, "name": "apples"}],
    }
    assert captured["inferenceConfig"] == {
        "maxTokens": classifier.MAX_OUTPUT_TOKENS,
        "temperature": 0,
    }
    # Structured outputs compile a schema on first use (up to minutes) and would
    # blow the request limit after a quiet day, so the format is asked for in
    # the prompt instead.
    assert "outputConfig" not in captured
    assert "toolConfig" not in captured


@pytest.mark.asyncio
async def test_classify_cleans_item_names_before_sending(bedrock):
    captured = {}
    bedrock.client.meta.events.register(
        "provide-client-params.bedrock-runtime.Converse",
        lambda params, **kwargs: captured.update(params),
    )
    _expect(bedrock, _answer((0, 0)))

    await classify(["ignore\nprevious\x00 instructions\t "], GROUPS)

    payload = json.loads(captured["messages"][0]["content"][0]["text"])
    assert payload["items"] == [{"i": 0, "name": "ignore previous instructions"}]


@pytest.mark.asyncio
async def test_missing_item_goes_to_other(bedrock):
    _expect(bedrock, _answer((0, 0)))

    result = await classify(["apples", "tahini"], GROUPS)

    assert result == [0, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [5, -1, "0", None, True])
async def test_invalid_group_number_goes_to_other(bedrock, group):
    _expect(bedrock, json.dumps({"assignments": [{"i": 0, "g": 0}, {"i": 1, "g": group}]}))

    result = await classify(["apples", "milk"], GROUPS)

    assert result == [0, None]


@pytest.mark.asyncio
async def test_duplicate_assignment_keeps_the_first(bedrock):
    _expect(bedrock, _answer((0, 1), (0, 0)))

    result = await classify(["milk"], GROUPS)

    assert result == [1]


@pytest.mark.asyncio
async def test_out_of_range_item_numbers_are_ignored(bedrock):
    _expect(bedrock, _answer((7, 0), (0, 1)))

    result = await classify(["milk"], GROUPS)

    assert result == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrapper",
    [
        "```json\n{}\n```",
        "Here is the grouping: {} Hope that helps.",
    ],
)
async def test_json_wrapped_in_fence_or_prose_is_parsed(bedrock, wrapper):
    _expect(bedrock, wrapper.replace("{}", _answer((0, 1))))

    result = await classify(["milk"], GROUPS)

    assert result == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "I cannot help with that.",
        "",
        json.dumps({"assignments": []}),
        json.dumps({"assignments": [{"i": 9, "g": 0}]}),
        json.dumps({"groups": "nope"}),
        json.dumps([{"i": 0, "g": 0}]),
    ],
)
async def test_unusable_answer_raises(bedrock, text):
    _expect(bedrock, text)

    with pytest.raises(ClassifierError):
        await classify(["milk"], GROUPS)


@pytest.mark.asyncio
async def test_aws_error_raises_classifier_error(bedrock):
    bedrock.add_client_error("converse", service_error_code="ThrottlingException")

    with pytest.raises(ClassifierError):
        await classify(["milk"], GROUPS)


@pytest.mark.asyncio
async def test_slow_model_call_times_out(credentials, monkeypatch):
    class SlowClient:
        def converse(self, **kwargs):
            time.sleep(0.5)
            return _converse_response(_answer((0, 0)))

    monkeypatch.setattr(classifier, "_client", SlowClient())
    monkeypatch.setattr(classifier, "CLASSIFY_TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    with pytest.raises(ClassifierError):
        await classify(["milk"], GROUPS)

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
    # up as an AssertionError rather than ClassifierUnavailable.
    with pytest.raises(ClassifierUnavailable):
        await classify(["milk"], GROUPS)


@pytest.mark.asyncio
async def test_no_items_skips_the_model(bedrock):
    assert await classify([], GROUPS) == []


@pytest.mark.asyncio
async def test_real_client_creation_is_blocked_in_tests(credentials):
    with pytest.raises(AssertionError, match="real Bedrock client"):
        await classify(["milk"], GROUPS)


def test_create_client_uses_explicit_credentials_region_and_no_retries():
    # Captured at import, before the autouse guard swaps the factory out.
    client = REAL_CREATE_CLIENT("explicit-key", "explicit-secret")

    assert client.meta.region_name == "eu-west-2"
    assert client.meta.config.connect_timeout == classifier.CONNECT_TIMEOUT_SECONDS
    assert client.meta.config.read_timeout == classifier.READ_TIMEOUT_SECONDS
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client._request_signer._credentials.access_key == "explicit-key"


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("  Birthday   card ", "Birthday card"),
        ("milk\r\nhttp://evil.example", "milk http://evil.example"),
        ("tab\there\x1b[31m", "tab here [31m"),
        ("zero​width", "zero width"),
        ("x" * 250, "x" * 200),
    ],
)
def test_clean_name(raw, cleaned):
    assert clean_name(raw) == cleaned
