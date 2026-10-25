import asyncio
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException
import pytest

from app.model_errors import ModelError
from app.grouping import MAX_GROUPING_ITEMS, GroupingTarget, group_items
from app.schemas import group_name_key

USER_ID = "user-123"


async def seed_groups(db, *names):
    await db.groups.insert_many(
        [
            {"name": name, "name_key": group_name_key(name), "position": position}
            for position, name in enumerate(names)
        ]
    )
    docs = await db.groups.find({}).sort("position", 1).to_list(length=None)
    return {doc["name"]: str(doc["_id"]) for doc in docs}


async def make_list(db, completed=False):
    result = await db.lists.insert_one(
        {"user_id": USER_ID, "name": "Groceries", "completed": completed}
    )
    return str(result.inserted_id)


async def add_item(db, list_id, name, sort_order, purchased=False, collection="items"):
    parent = "list_id" if collection == "items" else "template_id"
    result = await db[collection].insert_one(
        {
            "user_id": USER_ID,
            parent: list_id,
            "name": name,
            "sort_order": sort_order,
            "purchased": purchased,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )
    return str(result.inserted_id)


def list_target(db, list_id, touches=None):
    async def ensure_editable():
        doc = await db.lists.find_one({"_id": ObjectId(list_id), "user_id": USER_ID})
        if not doc:
            raise HTTPException(status_code=404, detail="List not found.")
        if doc.get("completed"):
            raise HTTPException(status_code=409, detail="Completed.")

    async def touch(now):
        if touches is not None:
            touches.append(now)

    return GroupingTarget(
        kind="list",
        target_id=list_id,
        user_id=USER_ID,
        items_collection="items",
        parent_field="list_id",
        ensure_editable=ensure_editable,
        touch=touch,
    )


def names(result):
    return [item["name"] for item in result["items"]]


async def stored(db, list_id):
    docs = await db.items.find({"list_id": list_id}).to_list(length=None)
    return {doc["name"]: doc for doc in docs}


async def run_with_edits(db, target, fake, edits):
    """Starts grouping, applies `edits` while the model is "thinking", then answers."""
    fake.gate = asyncio.Event()
    task = asyncio.create_task(group_items(db, target))
    await asyncio.wait_for(fake.entered.wait(), 2)
    await edits()
    fake.gate.set()
    return await task


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groups_bought_and_unbought_items_in_one_group_order_with_other_last(
    db, fake_classifier
):
    # Covers AE1, extended to bought items.
    await seed_groups(db, "Fruit", "Meat", "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    await add_item(db, list_id, "apples", 1)
    card = await add_item(db, list_id, "birthday card", 2)
    await add_item(db, list_id, "chicken", 3)
    await add_item(db, list_id, "cheese", 4, purchased=True)
    await add_item(db, list_id, "pears", 5, purchased=True)
    fake_classifier.assignments = {
        "milk": "Dairy", "apples": "Fruit", "chicken": "Meat",
        "cheese": "Dairy", "pears": "Fruit",
    }

    result = await group_items(db, list_target(db, list_id))

    assert names(result) == ["apples", "pears", "chicken", "milk", "cheese", "birthday card"]
    assert [item["sort_order"] for item in result["items"]] == [0, 1, 2, 3, 4, 5]
    assert result["unmatched"] == [{"item_id": card, "name": "birthday card"}]
    run = await db.grouping_runs.find_one({"_id": ObjectId(result["run_id"])})
    assert run["user_id"] == USER_ID
    assert run["target_kind"] == "list"
    assert run["target_id"] == list_id
    assert run["unmatched"] == [{"item_id": card, "name": "birthday card"}]
    assert run["sent_at"] is None
    docs = await stored(db, list_id)
    assert docs["milk"]["group_name"] == "Dairy"
    assert docs["apples"]["group_name"] == "Fruit"
    assert docs["birthday card"]["group_name"] == "Other"
    assert docs["cheese"]["group_name"] == "Dairy"
    assert docs["pears"]["group_name"] == "Fruit"


@pytest.mark.asyncio
async def test_bought_names_are_sent_to_the_model_too(db, fake_classifier):
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    await add_item(db, list_id, "bread", 1, purchased=True)

    await group_items(db, list_target(db, list_id))

    assert fake_classifier.calls == [(["milk", "bread"], ["Fruit", "Dairy"])]


@pytest.mark.asyncio
async def test_bought_item_in_other_is_offered_to_admins(db, fake_classifier):
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    await add_item(db, list_id, "apples", 0)
    card = await add_item(db, list_id, "birthday card", 1, purchased=True)
    fake_classifier.assignments = {"apples": "Fruit"}

    result = await group_items(db, list_target(db, list_id))

    assert result["unmatched"] == [{"item_id": card, "name": "birthday card"}]
    assert (await stored(db, list_id))["birthday card"]["group_name"] == "Other"


@pytest.mark.asyncio
async def test_list_with_only_bought_items_is_grouped(db, fake_classifier):
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0, purchased=True)
    await add_item(db, list_id, "apples", 1, purchased=True)
    fake_classifier.assignments = {"milk": "Dairy", "apples": "Fruit"}

    result = await group_items(db, list_target(db, list_id))

    assert names(result) == ["apples", "milk"]


@pytest.mark.asyncio
async def test_items_in_the_same_group_keep_their_current_order(db, fake_classifier):
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    for position, name in enumerate(["yoghurt", "pears", "milk", "apples", "cheese"]):
        await add_item(db, list_id, name, position)
    fake_classifier.assignments = {
        "yoghurt": "Dairy", "milk": "Dairy", "cheese": "Dairy",
        "pears": "Fruit", "apples": "Fruit",
    }

    result = await group_items(db, list_target(db, list_id))

    assert names(result) == ["pears", "apples", "yoghurt", "milk", "cheese"]


@pytest.mark.asyncio
async def test_missing_answer_lands_in_other_and_is_unmatched(db, fake_classifier):
    # Covers AE6: the classifier reports items it got no answer for as None.
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    await add_item(db, list_id, "tahini", 0)
    await add_item(db, list_id, "apples", 1)
    fake_classifier.assignments = {"apples": "Fruit"}

    result = await group_items(db, list_target(db, list_id))

    assert names(result) == ["apples", "tahini"]
    assert [entry["name"] for entry in result["unmatched"]] == ["tahini"]


@pytest.mark.asyncio
async def test_second_tap_follows_reordered_groups(db, fake_classifier):
    # Covers AE7.
    groups = await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    await add_item(db, list_id, "apples", 1)
    fake_classifier.assignments = {"milk": "Dairy", "apples": "Fruit"}
    first = await group_items(db, list_target(db, list_id))
    await db.groups.update_one({"_id": ObjectId(groups["Dairy"])}, {"$set": {"position": 0}})
    await db.groups.update_one({"_id": ObjectId(groups["Fruit"])}, {"$set": {"position": 1}})

    second = await group_items(db, list_target(db, list_id))

    assert names(first) == ["apples", "milk"]
    assert names(second) == ["milk", "apples"]


@pytest.mark.asyncio
async def test_all_matched_saves_no_run(db, fake_classifier):
    await seed_groups(db, "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    fake_classifier.assignments = {"milk": "Dairy"}

    result = await group_items(db, list_target(db, list_id))

    assert result["run_id"] is None
    assert result["unmatched"] == []
    assert await db.grouping_runs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_list_version_is_touched_once(db, fake_classifier):
    await seed_groups(db, "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    touches = []

    await group_items(db, list_target(db, list_id, touches))

    assert len(touches) == 1


# ---------------------------------------------------------------------------
# Edits made while the model is working (R8 / AE9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_added_during_wait_goes_to_the_end(db, fake_classifier):
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    await add_item(db, list_id, "apples", 1)
    await add_item(db, list_id, "bread", 2, purchased=True)
    fake_classifier.assignments = {"milk": "Dairy", "apples": "Fruit"}

    async def edits():
        await add_item(db, list_id, "eggs", 3)

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert names(result) == ["apples", "milk", "bread", "eggs"]
    assert "group_name" not in (await stored(db, list_id))["eggs"]


@pytest.mark.asyncio
async def test_item_bought_during_wait_keeps_its_group_place(db, fake_classifier):
    # Covers AE9 (same user on another device).
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    milk = await add_item(db, list_id, "milk", 0)
    await add_item(db, list_id, "apples", 1)
    await add_item(db, list_id, "bread", 2, purchased=True)
    fake_classifier.assignments = {"milk": "Dairy", "apples": "Fruit"}

    async def edits():
        await db.items.update_one({"_id": ObjectId(milk)}, {"$set": {"purchased": True}})
        await add_item(db, list_id, "eggs", 3)

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert names(result) == ["apples", "milk", "bread", "eggs"]
    assert (await stored(db, list_id))["milk"]["purchased"] is True


@pytest.mark.asyncio
async def test_other_item_ticked_during_wait_is_still_offered_to_admins(db, fake_classifier):
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    card = await add_item(db, list_id, "birthday card", 0)
    await add_item(db, list_id, "apples", 1)
    fake_classifier.assignments = {"apples": "Fruit"}

    async def edits():
        await db.items.update_one({"_id": ObjectId(card)}, {"$set": {"purchased": True}})

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert names(result) == ["apples", "birthday card"]
    assert result["unmatched"] == [{"item_id": card, "name": "birthday card"}]


@pytest.mark.asyncio
async def test_item_deleted_during_wait_is_dropped(db, fake_classifier):
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    card = await add_item(db, list_id, "birthday card", 0)
    await add_item(db, list_id, "apples", 1)
    fake_classifier.assignments = {"apples": "Fruit"}

    async def edits():
        await db.items.delete_one({"_id": ObjectId(card)})

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert names(result) == ["apples"]
    assert result["unmatched"] == []
    assert result["run_id"] is None


@pytest.mark.asyncio
async def test_item_renamed_during_wait_is_placed_by_its_old_name(db, fake_classifier):
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    gift = await add_item(db, list_id, "gift", 0)
    await add_item(db, list_id, "milk", 1)
    fake_classifier.assignments = {"milk": "Dairy"}

    async def edits():
        await db.items.update_one({"_id": ObjectId(gift)}, {"$set": {"name": "flowers"}})

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert names(result) == ["milk", "flowers"]
    assert result["unmatched"] == [{"item_id": gift, "name": "gift"}]


@pytest.mark.asyncio
async def test_item_unticked_during_wait_keeps_its_group_place(db, fake_classifier):
    await seed_groups(db, "Bakery", "Fruit")
    list_id = await make_list(db)
    await add_item(db, list_id, "apples", 0)
    bread = await add_item(db, list_id, "bread", 1, purchased=True)
    fake_classifier.assignments = {"apples": "Fruit", "bread": "Bakery"}

    async def edits():
        await db.items.update_one({"_id": ObjectId(bread)}, {"$set": {"purchased": False}})

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert names(result) == ["bread", "apples"]
    assert (await stored(db, list_id))["bread"]["group_name"] == "Bakery"


@pytest.mark.asyncio
async def test_bought_item_sits_between_unbought_items_of_other_groups(db, fake_classifier):
    # Unticking it later must put it back in its group, not after every unbought item.
    await seed_groups(db, "Fruit", "Dairy")
    list_id = await make_list(db)
    await add_item(db, list_id, "milk", 0)
    await add_item(db, list_id, "pears", 1, purchased=True)
    await add_item(db, list_id, "apples", 2)
    fake_classifier.assignments = {"milk": "Dairy", "pears": "Fruit", "apples": "Fruit"}

    result = await group_items(db, list_target(db, list_id))

    assert names(result) == ["pears", "apples", "milk"]


@pytest.mark.asyncio
async def test_every_item_deleted_during_wait_returns_empty(db, fake_classifier):
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    await add_item(db, list_id, "apples", 0)

    async def edits():
        await db.items.delete_many({"list_id": list_id})

    result = await run_with_edits(db, list_target(db, list_id), fake_classifier, edits)

    assert result == {"items": [], "run_id": None, "unmatched": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "status_code"),
    [({"$set": {"completed": True}}, 409), (None, 404)],
)
async def test_target_completed_or_deleted_during_wait_writes_nothing(
    db, fake_classifier, change, status_code
):
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    await add_item(db, list_id, "apples", 7)
    await add_item(db, list_id, "card", 3)
    touches = []

    async def edits():
        if change is None:
            await db.lists.delete_one({"_id": ObjectId(list_id)})
        else:
            await db.lists.update_one({"_id": ObjectId(list_id)}, change)

    with pytest.raises(HTTPException) as raised:
        await run_with_edits(db, list_target(db, list_id, touches), fake_classifier, edits)

    assert raised.value.status_code == status_code
    docs = await stored(db, list_id)
    assert [docs["apples"]["sort_order"], docs["card"]["sort_order"]] == [7, 3]
    assert all("group_name" not in doc for doc in docs.values())
    assert touches == []
    assert await db.grouping_runs.count_documents({}) == 0


# ---------------------------------------------------------------------------
# Refusals and failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classifier_failure_returns_503_and_changes_nothing(db, fake_classifier):
    # Covers AE5.
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    await add_item(db, list_id, "apples", 5)
    touches = []
    fake_classifier.error = ModelError("down")

    with pytest.raises(HTTPException) as raised:
        await group_items(db, list_target(db, list_id, touches))

    assert raised.value.status_code == 503
    doc = (await stored(db, list_id))["apples"]
    assert doc["sort_order"] == 5
    assert "group_name" not in doc
    assert touches == []
    assert await db.grouping_runs.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "status_code"),
    [
        ("empty", 422),
        ("too_many", 422),
        ("too_many_with_bought", 422),
        ("no_groups", 409),
        ("completed", 409),
    ],
)
async def test_refusals_never_call_the_model(db, fake_classifier, setup, status_code):
    if setup != "no_groups":
        await seed_groups(db, "Fruit")
    list_id = await make_list(db, completed=setup == "completed")
    if setup == "too_many":
        for index in range(MAX_GROUPING_ITEMS + 1):
            await add_item(db, list_id, f"item {index}", index)
    elif setup == "too_many_with_bought":
        for index in range(MAX_GROUPING_ITEMS):
            await add_item(db, list_id, f"item {index}", index)
        await add_item(db, list_id, "bought", MAX_GROUPING_ITEMS, purchased=True)
    elif setup != "empty":
        await add_item(db, list_id, "apples", 0)

    with pytest.raises(HTTPException) as raised:
        await group_items(db, list_target(db, list_id))

    assert raised.value.status_code == status_code
    assert fake_classifier.calls == []


@pytest.mark.asyncio
async def test_exactly_the_cap_is_allowed(db, fake_classifier):
    await seed_groups(db, "Fruit")
    list_id = await make_list(db)
    for index in range(MAX_GROUPING_ITEMS - 1):
        await add_item(db, list_id, f"item {index}", index)
    await add_item(db, list_id, "bought", MAX_GROUPING_ITEMS, purchased=True)

    result = await group_items(db, list_target(db, list_id))

    assert len(result["items"]) == MAX_GROUPING_ITEMS
