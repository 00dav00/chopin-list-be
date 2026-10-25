import pytest

from app.group_order import GroupOrder

GROUPS = [{"id": "fruit-id", "name": "Fruit"}, {"id": "dairy-id", "name": "Dairy"}]
FRUIT, DAIRY, OTHER = "Fruit", "Dairy", "Other"


def item(item_id, name=None, purchased=False):
    return {"_id": item_id, "name": name or item_id, "purchased": purchased}


def ids(items):
    return [entry["_id"] for entry in items]


def order_for(*pairs):
    """GroupOrder built from (item, group name) pairs, as the classifier returns them."""
    items = [entry for entry, _ in pairs]
    return GroupOrder(items, GROUPS, {entry["name"]: group for entry, group in pairs})


# --- sort ------------------------------------------------------------------


def test_sorts_by_group_order_with_other_last():
    order = order_for((item("card"), OTHER), (item("milk"), DAIRY), (item("apples"), FRUIT))

    assert ids(order.sort([item("card"), item("milk"), item("apples")])) == [
        "apples", "milk", "card",
    ]


def test_bought_and_unbought_share_one_order():
    order = order_for(
        (item("milk"), DAIRY), (item("pears", purchased=True), FRUIT), (item("apples"), FRUIT)
    )

    current = [item("milk"), item("pears", purchased=True), item("apples")]

    assert ids(order.sort(current)) == ["pears", "apples", "milk"]


def test_ties_keep_their_current_relative_order():
    order = order_for(
        (item("yoghurt"), DAIRY), (item("milk"), DAIRY), (item("cheese"), DAIRY)
    )

    # The current order is what counts, not the order the model saw.
    current = [item("cheese"), item("yoghurt"), item("milk")]

    assert ids(order.sort(current)) == ["cheese", "yoghurt", "milk"]


def test_items_the_model_never_saw_go_last_after_other():
    order = order_for((item("card"), OTHER), (item("milk"), DAIRY))

    current = [item("eggs"), item("card"), item("bread"), item("milk")]

    assert ids(order.sort(current)) == ["milk", "card", "eggs", "bread"]


def test_items_deleted_during_the_wait_are_not_brought_back():
    order = order_for((item("apples"), FRUIT), (item("card"), OTHER))

    assert ids(order.sort([item("apples")])) == ["apples"]


def test_ids_are_matched_as_strings():
    order = GroupOrder([{"_id": 7, "name": "milk"}], GROUPS, {"milk": DAIRY})

    assert order.is_classified("7")
    assert order.group_name("7") == "Dairy"


# --- group_name / is_classified --------------------------------------------


def test_group_name_is_the_group_name_or_other():
    order = order_for((item("milk"), DAIRY), (item("card"), OTHER))

    assert order.group_name("milk") == "Dairy"
    assert order.group_name("card") == "Other"


def test_item_missing_from_the_answer_or_with_an_unknown_group_goes_to_other():
    items = [item("milk"), item("tahini"), item("card")]

    order = GroupOrder(items, GROUPS, {"milk": DAIRY, "card": "Cards & gifts"})

    assert order.group_name("tahini") == "Other"
    assert order.group_name("card") == "Other"
    assert ids(order.sort(items)) == ["milk", "tahini", "card"]


def test_only_items_the_model_saw_are_classified():
    order = order_for((item("milk"), DAIRY), (item("card"), OTHER))

    assert order.is_classified("milk")
    assert order.is_classified("card")
    assert not order.is_classified("eggs")


def test_group_name_of_an_unseen_item_raises():
    # Callers check is_classified first; an unseen item must stay "never grouped".
    with pytest.raises(KeyError):
        order_for((item("milk"), DAIRY)).group_name("eggs")


# --- unmatched -------------------------------------------------------------


def test_unmatched_lists_other_items_in_the_given_order():
    order = order_for(
        (item("tahini"), OTHER), (item("milk"), DAIRY), (item("card", purchased=True), OTHER)
    )

    current = [item("card", purchased=True), item("milk"), item("tahini")]

    assert order.unmatched(current) == [
        {"item_id": "card", "name": "card"},
        {"item_id": "tahini", "name": "tahini"},
    ]


def test_unmatched_uses_the_name_the_model_judged():
    order = order_for((item("gift", name="gift"), OTHER))

    renamed = [item("gift", name="flowers")]

    assert order.unmatched(renamed) == [{"item_id": "gift", "name": "gift"}]


def test_unmatched_skips_deleted_and_unseen_items():
    order = order_for((item("card"), OTHER), (item("tahini"), OTHER))

    assert order.unmatched([item("tahini"), item("eggs")]) == [
        {"item_id": "tahini", "name": "tahini"}
    ]
