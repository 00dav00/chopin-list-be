"""Groups all of a list's or template's items, bought ones included, by kind of product.

Shared by the list and template group endpoints; the routers describe the
target (collection, re-checks, version bump) and this module does the rest.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException, status
from pymongo import UpdateOne

from . import classifier
from .bedrock import ModelError
from .routers.groups import list_stored_groups
from .utils import serialize_doc, to_object_id, utcnow

MAX_GROUPING_ITEMS = 100
NO_ITEMS_MESSAGE = "There are no items to group."
TOO_MANY_ITEMS_MESSAGE = f"Grouping works on at most {MAX_GROUPING_ITEMS} items at a time."
NOT_SET_UP_MESSAGE = "Grouping is not set up yet: no groups are configured."
UNAVAILABLE_MESSAGE = "Couldn't group right now. Try again in a moment."


@dataclass(frozen=True)
class GroupingTarget:
    kind: str  # "list" or "template"
    target_id: str
    user_id: str
    items_collection: str
    parent_field: str
    # Raises 404/409 if the target is gone or no longer editable.
    ensure_editable: Callable[[], Awaitable[None]]
    # Bumps the version other open views poll on; None when there is none.
    touch: Optional[Callable[[datetime], Awaitable[None]]] = None


async def _load_items(db, target: GroupingTarget) -> list[dict]:
    cursor = db[target.items_collection].find(
        {target.parent_field: target.target_id, "user_id": target.user_id}
    ).sort([("sort_order", 1), ("created_at", 1)])
    return await cursor.to_list(length=None)


def _merge_order(current: list[dict], ranks: dict[str, int]) -> list[dict]:
    """One order for bought and unbought alike, so an item unticked later sits in
    its group. Items the model never saw (added during the wait) go last; the
    sort is stable, so ties keep their current relative order."""
    return sorted(current, key=lambda item: ranks.get(str(item["_id"]), float("inf")))


async def group_items(db, target: GroupingTarget) -> dict:
    await target.ensure_editable()
    groups = await list_stored_groups(db)
    items = await _load_items(db, target)
    if not items:
        raise HTTPException(status_code=422, detail=NO_ITEMS_MESSAGE)
    if len(items) > MAX_GROUPING_ITEMS:
        raise HTTPException(status_code=422, detail=TOO_MANY_ITEMS_MESSAGE)
    if not groups:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_SET_UP_MESSAGE)

    try:
        assignments = await classifier.classify(
            [item["name"] for item in items], [group["name"] for group in groups]
        )
    except ModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE_MESSAGE
        ) from exc

    other_rank = len(groups)
    ranks: dict[str, int] = {}
    judged_names: dict[str, str] = {}
    for item, group_index in zip(items, assignments):
        item_id = str(item["_id"])
        ranks[item_id] = other_rank if group_index is None else group_index
        judged_names[item_id] = item["name"]

    # The model call takes seconds; the target may have been completed or
    # deleted, and items changed from another tab, in the meantime.
    await target.ensure_editable()
    current = await _load_items(db, target)
    ordered = _merge_order(current, ranks)

    now = utcnow()
    operations = []
    unmatched = []
    for sort_order, item in enumerate(ordered):
        item_id = str(item["_id"])
        update = {"sort_order": sort_order, "updated_at": now}
        if item_id in ranks:
            rank = ranks[item_id]
            update["group_id"] = None if rank == other_rank else groups[rank]["id"]
            if rank == other_rank:
                unmatched.append({"item_id": item_id, "name": judged_names[item_id]})
        operations.append(
            UpdateOne(
                {"_id": to_object_id(item_id, "item_id"), "user_id": target.user_id},
                {"$set": update},
            )
        )
    if operations:
        await db[target.items_collection].bulk_write(operations)
    if target.touch is not None:
        # Fresh timestamp: an edit landing after `now` would otherwise win the
        # $max and leave other tabs on a version from before the regroup.
        await target.touch(utcnow())

    run_id = None
    if unmatched:
        result = await db.grouping_runs.insert_one(
            {
                "user_id": target.user_id,
                "target_kind": target.kind,
                "target_id": target.target_id,
                "unmatched": unmatched,
                "created_at": now,
                "sent_at": None,
            }
        )
        run_id = str(result.inserted_id)

    return {
        "items": [serialize_doc(item) for item in await _load_items(db, target)],
        "run_id": run_id,
        "unmatched": unmatched,
    }
