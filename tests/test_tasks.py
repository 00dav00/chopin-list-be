import pytest

from app.model_errors import ModelError
from app.tasks import (
    DEFAULT_GROUPS,
    SAMPLE_ITEMS,
    classify_with_stored_groups,
    format_assignments,
    sample_items,
    seed_default_groups,
    set_user_admin_by_email,
    toggle_user_approved_by_email,
)


@pytest.mark.asyncio
async def test_toggle_user_approved_by_email_updates_user(db):
    email = "user@example.com"
    await db.users.insert_one(
        {
            "google_sub": "sub-123",
            "email": email,
            "approved": False,
        }
    )

    approved = await toggle_user_approved_by_email(db=db, email=email)
    assert approved is True

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["approved"] is True

    approved = await toggle_user_approved_by_email(db=db, email=email)
    assert approved is False

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["approved"] is False


@pytest.mark.asyncio
async def test_toggle_user_approved_by_email_returns_none_when_user_not_found(db):
    approved = await toggle_user_approved_by_email(db=db, email="missing@example.com")
    assert approved is None


@pytest.mark.asyncio
async def test_set_user_admin_by_email_updates_user(db):
    email = "user@example.com"
    await db.users.insert_one(
        {
            "google_sub": "sub-123",
            "email": email,
            "admin": False,
        }
    )

    admin = await set_user_admin_by_email(db=db, email=email, is_admin=True)
    assert admin is True

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["admin"] is True

    admin = await set_user_admin_by_email(db=db, email=email, is_admin=False)
    assert admin is False

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["admin"] is False


@pytest.mark.asyncio
async def test_set_user_admin_by_email_returns_none_when_user_not_found(db):
    admin = await set_user_admin_by_email(
        db=db,
        email="missing@example.com",
        is_admin=True,
    )
    assert admin is None


@pytest.mark.asyncio
async def test_seed_default_groups_inserts_defaults_in_order(db):
    inserted = await seed_default_groups(db=db)

    assert inserted == len(DEFAULT_GROUPS)
    docs = await db.groups.find({}).sort("position", 1).to_list(length=None)
    assert [doc["name"] for doc in docs] == DEFAULT_GROUPS
    assert [doc["position"] for doc in docs] == list(range(len(DEFAULT_GROUPS)))
    assert all(doc["name_key"] == doc["name"].casefold() for doc in docs)


@pytest.mark.asyncio
async def test_seed_default_groups_refuses_when_any_group_exists(db):
    await db.groups.insert_one({"name": "Dairy", "name_key": "dairy", "position": 0})

    inserted = await seed_default_groups(db=db)

    assert inserted is None
    assert await db.groups.count_documents({}) == 1


@pytest.mark.asyncio
async def test_seed_default_groups_twice_inserts_once(db):
    first = await seed_default_groups(db=db)
    second = await seed_default_groups(db=db)

    assert first == len(DEFAULT_GROUPS)
    assert second is None
    assert await db.groups.count_documents({}) == len(DEFAULT_GROUPS)


def test_default_groups_are_unique_and_never_other():
    keys = [name.casefold() for name in DEFAULT_GROUPS]
    assert len(keys) == len(set(keys))
    assert "other" not in keys


def test_seed_default_groups_command_reports_count(monkeypatch):
    from typer.testing import CliRunner

    from app import tasks

    async def inserted():
        return 17

    monkeypatch.setattr(tasks, "_seed_default_groups", inserted)

    result = CliRunner().invoke(tasks.app, ["seed-default-groups"])

    assert result.exit_code == 0
    assert "Inserted 17 default groups." in result.output


def test_seed_default_groups_command_fails_when_groups_exist(monkeypatch):
    from typer.testing import CliRunner

    from app import tasks

    async def refused():
        return None

    monkeypatch.setattr(tasks, "_seed_default_groups", refused)

    result = CliRunner().invoke(tasks.app, ["seed-default-groups"])

    assert result.exit_code == 1
    assert "Groups already exist; nothing inserted." in result.output


# --- classify-items ----------------------------------------------------------


def test_sample_items_numbers_repeats_so_names_stay_unique():
    names = sample_items(len(SAMPLE_ITEMS) + 2)

    assert names[: len(SAMPLE_ITEMS)] == SAMPLE_ITEMS
    assert names[-2:] == [f"{SAMPLE_ITEMS[0]} 2", f"{SAMPLE_ITEMS[1]} 2"]
    assert len(set(names)) == len(names)


@pytest.mark.asyncio
async def test_classify_with_stored_groups_sends_groups_in_position_order(db, fake_classifier):
    await db.groups.insert_many(
        [{"name": "Dairy", "position": 1}, {"name": "Fruit", "position": 0}]
    )
    fake_classifier.assignments = {"apples": "Fruit", "milk": "Dairy"}

    groups, assignments = await classify_with_stored_groups(db, ["apples", "milk", "bolts"])

    assert groups == ["Fruit", "Dairy"]
    assert fake_classifier.calls == [(["apples", "milk", "bolts"], ["Fruit", "Dairy"])]
    assert assignments == {"apples": "Fruit", "milk": "Dairy", "bolts": "Other"}


@pytest.mark.asyncio
async def test_classify_with_stored_groups_skips_the_model_without_groups(db, fake_classifier):
    assert await classify_with_stored_groups(db, ["apples"]) == ([], {})
    assert fake_classifier.calls == []


def test_format_assignments_lists_groups_in_order_with_other_last_and_skips_empty():
    lines = format_assignments(
        ["Fruit", "Dairy", "Bakery"],
        {"bolts": "Other", "milk": "Dairy", "apples": "Fruit", "pears": "Fruit"},
    )

    assert lines == ["Fruit (2): apples, pears", "Dairy (1): milk", "Other (1): bolts"]


def _invoke(monkeypatch, args, result=None, error=None):
    from typer.testing import CliRunner

    from app import tasks
    from app.config import settings

    calls = []

    async def classify(names):
        calls.append(names)
        if error:
            raise error
        return result

    monkeypatch.setattr(tasks, "_classify_items", classify)
    monkeypatch.setattr(settings, "model_provider", "bedrock")
    return CliRunner().invoke(tasks.app, ["classify-items", *args]), calls


def test_classify_items_command_prints_groups_and_summary(monkeypatch):
    output, calls = _invoke(
        monkeypatch, ["apples", "bolts", "apples"], result=(["Fruit"], {"apples": "Fruit", "bolts": "Other"})
    )

    assert output.exit_code == 0
    assert calls == [["apples", "bolts"]]
    assert "Provider: bedrock" in output.output
    assert "Fruit (1): apples" in output.output
    assert "Other (1): bolts" in output.output
    assert "1 of 2 items in Other" in output.output


def test_classify_items_command_reads_a_file_and_overrides_the_provider(monkeypatch, tmp_path):
    from app.config import settings

    path = tmp_path / "items.txt"
    path.write_text("apples\n\n  milk  \n")
    output, calls = _invoke(
        monkeypatch, ["--file", str(path), "--provider", "mistral"], result=(["Fruit"], {})
    )

    assert output.exit_code == 0
    assert calls == [["apples", "milk"]]
    assert f"Provider: mistral, model: {settings.mistral_model}" in output.output


def test_classify_items_command_uses_samples_when_no_items_given(monkeypatch):
    output, calls = _invoke(monkeypatch, ["--count", "3"], result=(["Fruit"], {}))

    assert output.exit_code == 0
    assert calls == [SAMPLE_ITEMS[:3]]


def test_classify_items_command_reports_model_failure(monkeypatch):
    output, _ = _invoke(monkeypatch, ["apples"], error=ModelError("Model call failed: HTTP 401"))

    assert output.exit_code == 1
    assert "HTTP 401" in output.output


def test_classify_items_command_fails_without_groups(monkeypatch):
    output, _ = _invoke(monkeypatch, ["apples"], result=([], {}))

    assert output.exit_code == 1
    assert "seed-default-groups" in output.output
