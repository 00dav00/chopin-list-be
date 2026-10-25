"""Orders items by the group the model gave each one; no database access."""

from .routers.groups import OTHER_GROUP_NAME


class GroupOrder:
    """The model's answer, keyed by item id so it applies to the items as they
    are after the wait: items added meanwhile go last, deleted ones drop out."""

    def __init__(self, items: list[dict], groups: list[dict], assignments: dict[str, str]):
        self._other_rank = len(groups)
        rank_by_group = {group["name"]: rank for rank, group in enumerate(groups)}
        self._ranks: dict[str, int] = {}
        self._group_names: dict[str, str] = {}
        self._judged_names: dict[str, str] = {}
        for item in items:
            item_id = str(item["_id"])
            group = assignments.get(item["name"], OTHER_GROUP_NAME)
            rank = rank_by_group.get(group, self._other_rank)
            self._ranks[item_id] = rank
            self._group_names[item_id] = group if rank != self._other_rank else OTHER_GROUP_NAME
            self._judged_names[item_id] = item["name"]

    def sort(self, items: list[dict]) -> list[dict]:
        """One order for bought and unbought alike, so an item unticked later sits in
        its group. Items the model never saw go last; the sort is stable, so ties
        keep their current relative order."""
        return sorted(items, key=lambda item: self._ranks.get(str(item["_id"]), float("inf")))

    def is_classified(self, item_id: str) -> bool:
        return item_id in self._ranks

    def group_name(self, item_id: str) -> str:
        """The item's group name, "Other" included. Only for classified items."""
        return self._group_names[item_id]

    def unmatched(self, items: list[dict]) -> list[dict]:
        """Items placed in "Other", under the name the model judged, so a rename
        during the wait doesn't change what admins are offered."""
        item_ids = [str(item["_id"]) for item in items]
        return [
            {"item_id": item_id, "name": self._judged_names[item_id]}
            for item_id in item_ids
            if self._ranks.get(item_id) == self._other_rank
        ]
