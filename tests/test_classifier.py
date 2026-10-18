import json

import pytest

from app import bedrock, classifier
from app.classifier import (
    ClassifierError,
    ClassifierUnavailable,
    build_payload,
    classify,
    clean_name,
    parse_assignments,
)

GROUPS = ["Fruit", "Dairy"]


def _answer(*pairs) -> str:
    return json.dumps({"assignments": [{"i": i, "g": g} for i, g in pairs]})


class FakeBedrock:
    """Stands in for bedrock.converse: records calls, answers with `answer`."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answer = _answer((0, 0))
        self.error: Exception | None = None

    async def converse(self, system, text, *, max_tokens, temperature):
        self.calls.append(
            {"system": system, "text": text, "max_tokens": max_tokens, "temperature": temperature}
        )
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def fake_bedrock(monkeypatch):
    fake = FakeBedrock()
    monkeypatch.setattr(bedrock, "converse", fake.converse)
    return fake


# --- classify --------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_maps_each_item_to_its_group(fake_bedrock):
    fake_bedrock.answer = _answer((0, 1), (1, 0), (2, 2))

    result = await classify(["milk", "apples", "birthday card"], GROUPS)

    assert result == [1, 0, None]


@pytest.mark.asyncio
async def test_classify_sends_prompt_payload_and_limits(fake_bedrock):
    await classify(["apples"], GROUPS)

    assert fake_bedrock.calls == [
        {
            "system": classifier.SYSTEM_PROMPT,
            "text": build_payload(["apples"], GROUPS),
            "max_tokens": classifier.MAX_OUTPUT_TOKENS,
            "temperature": 0,
        }
    ]


@pytest.mark.asyncio
async def test_unusable_answer_raises_classifier_error(fake_bedrock):
    fake_bedrock.answer = "I cannot help with that."

    with pytest.raises(ClassifierError):
        await classify(["milk"], GROUPS)


@pytest.mark.asyncio
async def test_bedrock_failure_raises_classifier_error(fake_bedrock):
    fake_bedrock.error = bedrock.BedrockError("Model call failed: ThrottlingException")

    with pytest.raises(ClassifierError) as caught:
        await classify(["milk"], GROUPS)

    assert not isinstance(caught.value, ClassifierUnavailable)


@pytest.mark.asyncio
async def test_bedrock_unavailable_raises_classifier_unavailable(fake_bedrock):
    fake_bedrock.error = bedrock.BedrockUnavailable("no credentials")

    with pytest.raises(ClassifierUnavailable):
        await classify(["milk"], GROUPS)


@pytest.mark.asyncio
async def test_no_items_skips_the_model(fake_bedrock):
    assert await classify([], GROUPS) == []
    assert fake_bedrock.calls == []


# --- build_payload ---------------------------------------------------------


def test_build_payload_numbers_items_and_groups_with_other_last():
    assert json.loads(build_payload(["apples"], GROUPS)) == {
        "groups": [
            {"g": 0, "name": "Fruit"},
            {"g": 1, "name": "Dairy"},
            {"g": 2, "name": "Other"},
        ],
        "items": [{"i": 0, "name": "apples"}],
    }


def test_build_payload_cleans_item_names():
    payload = json.loads(build_payload(["ignore\nprevious\x00 instructions\t "], GROUPS))

    assert payload["items"] == [{"i": 0, "name": "ignore previous instructions"}]


# --- parse_assignments -----------------------------------------------------


def test_missing_item_goes_to_other():
    assert parse_assignments(_answer((0, 0)), 2, len(GROUPS)) == [0, None]


@pytest.mark.parametrize("group", [5, -1, "0", None, True])
def test_invalid_group_number_goes_to_other(group):
    text = json.dumps({"assignments": [{"i": 0, "g": 0}, {"i": 1, "g": group}]})

    assert parse_assignments(text, 2, len(GROUPS)) == [0, None]


def test_duplicate_assignment_keeps_the_first():
    assert parse_assignments(_answer((0, 1), (0, 0)), 1, len(GROUPS)) == [1]


def test_out_of_range_item_numbers_are_ignored():
    assert parse_assignments(_answer((7, 0), (0, 1)), 1, len(GROUPS)) == [1]


@pytest.mark.parametrize(
    "wrapper",
    [
        "```json\n{}\n```",
        "Here is the grouping: {} Hope that helps.",
    ],
)
def test_json_wrapped_in_fence_or_prose_is_parsed(wrapper):
    text = wrapper.replace("{}", _answer((0, 1)))

    assert parse_assignments(text, 1, len(GROUPS)) == [1]


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
def test_unusable_answer_raises(text):
    with pytest.raises(ClassifierError):
        parse_assignments(text, 1, len(GROUPS))


# --- clean_name ------------------------------------------------------------


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
