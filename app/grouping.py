"""Groups a list's or template's unbought items by kind of product.

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
NO_UNBOUGHT_ITEMS_MESSAGE = "There are no unbought items to group."
TOO_MANY_ITEMS_MESSAGE = (
    f"Grouping works on at most {MAX_GROUPING_ITEMS} unbought items at a time."
)
NOT_SET_UP_MESSAGE = "Grouping is not set up yet: no groups are configured."
UNAVAILABLE_MESSAGE = "Couldn't group right now. Try again in a moment."


@dataclass(frozen=True)
class GroupingTarget:
    kind: str  # "list" or "template"
    target_id: str
    user_id: str
    items_collection: str
    parent_field: str
    has_purchased: bool
    # Raises 404/409 if the target is gone or no longer editable.
    ensure_editable: Callable[[], Awaitable[None]]
    # Bumps the version other open views poll on; None when there is none.
    touch: Optional[Callable[[datetime], Awaitable[None]]] = None


async def _load_items(db, target: GroupingTarget) -> list[dict]:
    cursor = db[target.items_collection].find(
        {target.parent_field: target.target_id, "user_id": target.user_id}
    ).sort([("sort_order", 1), ("created_at", 1)])
    return await cursor.to_list(length=None)


def _is_bought(target: GroupingTarget, item: dict) -> bool:
    return target.has_purchased and bool(item.get("purchased", False))


def _merge_order(
    target: GroupingTarget, current: list[dict], ranks: dict[str, int]
) -> list[dict]:
    """New order: classified unbought by group rank, then unbought items the
    model never saw (added or un-ticked during the wait), then bought items.
    Sorting is stable, so each bucket keeps its current relative order."""
    unbought = [item for item in current if not _is_bought(target, item)]
    classified = [item for item in unbought if str(item["_id"]) in ranks]
    unseen = [item for item in unbought if str(item["_id"]) not in ranks]
    bought = [item for item in current if _is_bought(target, item)]
    classified.sort(key=lambda item: ranks[str(item["_id"])])
    return classified + unseen + bought


async def group_items(db, target: GroupingTarget) -> dict:
    await target.ensure_editable()
    groups = await list_stored_groups(db)
    items = await _load_items(db, target)
    unbought = [item for item in items if not _is_bought(target, item)]
    if not unbought:
        raise HTTPException(status_code=422, detail=NO_UNBOUGHT_ITEMS_MESSAGE)
    if len(unbought) > MAX_GROUPING_ITEMS:
        raise HTTPException(status_code=422, detail=TOO_MANY_ITEMS_MESSAGE)
    if not groups:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_SET_UP_MESSAGE)

    try:
        assignments = await classifier.classify(
            [item["name"] for item in unbought], [group["name"] for group in groups]
        )
    except ModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE_MESSAGE
        ) from exc

    other_rank = len(groups)
    ranks: dict[str, int] = {}
    judged_names: dict[str, str] = {}
    for item, group_index in zip(unbought, assignments):
        item_id = str(item["_id"])
        ranks[item_id] = other_rank if group_index is None else group_index
        judged_names[item_id] = item["name"]

    # The model call takes seconds; the target may have been completed or
    # deleted, and items changed from another tab, in the meantime.
    await target.ensure_editable()
    current = await _load_items(db, target)
    ordered = _merge_order(target, current, ranks)

    now = utcnow()
    operations = []
    unmatched = []
    for sort_order, item in enumerate(ordered):
        item_id = str(item["_id"])
        update = {"sort_order": sort_order, "updated_at": now}
        if item_id in ranks and not _is_bought(target, item):
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
        await target.touch(now)

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
