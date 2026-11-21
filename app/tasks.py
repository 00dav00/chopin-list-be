import asyncio
import time
from pathlib import Path
from typing import Optional

import typer
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from . import classifier
from .config import settings
from .model_errors import ModelError
from .routers.groups import OTHER_GROUP_NAME, list_stored_groups
from .schemas import group_name_key
from .utils import utcnow

app = typer.Typer(help="Operational tasks for the Shoplist API.")


@app.callback()
def main():
    # Force multi-command mode so commands are invoked as:
    # `python -m app.tasks <command> ...`
    return None


async def toggle_user_approved_by_email(db, email: str) -> bool | None:
    user = await db.users.find_one_and_update(
        {"email": email},
        [
            {
                "$set": {
                    "approved": {"$not": {"$ifNull": ["$approved", False]}},
                }
            }
        ],
        return_document=ReturnDocument.AFTER,
    )
    if user is None:
        return None
    return bool(user.get("approved", False))


async def set_user_admin_by_email(db, email: str, is_admin: bool) -> bool | None:
    user = await db.users.find_one_and_update(
        {"email": email},
        {"$set": {"admin": is_admin}},
        return_document=ReturnDocument.AFTER,
    )
    if user is None:
        return None
    return bool(user.get("admin", False))


# Ordered roughly as a shopper walks a supermarket; "Other" is built in and
# always last, so it is not stored.
DEFAULT_GROUPS = [
    "Fruit",
    "Vegetables",
    "Bakery",
    "Vegetarian meat",
    "Meat",
    "Fish & seafood",
    "Dairy & eggs",
    "Cheese",
    "Pasta, rice & grains",
    "Canned & jarred",
    "Sauces, oils & spices",
    "Breakfast & spreads",
    "Snacks & sweets",
    "Drinks",
    "Frozen",
    "Household & cleaning",
    "Personal care",
]


async def seed_default_groups(db) -> int | None:
    """Returns how many groups were inserted, or None when any group exists."""
    if await db.groups.count_documents({}, limit=1):
        return None
    now = utcnow()
    docs = [
        {
            "name": name,
            "name_key": group_name_key(name),
            "position": position,
            "created_at": now,
            "updated_at": now,
        }
        for position, name in enumerate(DEFAULT_GROUPS)
    ]
    await db.groups.insert_many(docs)
    return len(docs)


async def _seed_default_groups() -> int | None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    try:
        return await seed_default_groups(db=client[settings.mongo_db])
    finally:
        client.close()


async def _toggle_user_approved(email: str) -> bool | None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    try:
        db = client[settings.mongo_db]
        return await toggle_user_approved_by_email(db=db, email=email)
    finally:
        client.close()


async def _set_user_admin(email: str, is_admin: bool) -> bool | None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    try:
        db = client[settings.mongo_db]
        return await set_user_admin_by_email(db=db, email=email, is_admin=is_admin)
    finally:
        client.close()


# A spread of everyday items, with a few that test the boundaries between groups.
SAMPLE_ITEMS = [
    "apples", "bananas", "carrots", "spinach", "sourdough loaf", "croissants",
    "tofu", "vegan burgers", "chicken thighs", "minced beef", "salmon fillets",
    "prawns", "milk", "oat milk", "eggs", "greek yoghurt", "cheddar", "parmesan",
    "spaghetti", "basmati rice", "chopped tomatoes", "chickpeas", "olive oil",
    "soy sauce", "cumin", "cornflakes", "peanut butter", "crisps",
    "dark chocolate", "orange juice", "sparkling water", "frozen peas",
    "ice cream", "bin bags", "washing-up liquid", "toilet roll", "toothpaste",
    "shampoo", "dog food", "birthday candles",
]


def sample_items(count: int) -> list[str]:
    """The first `count` sample items, numbered once the list runs out so names stay unique."""
    names = []
    for index in range(count):
        lap, position = divmod(index, len(SAMPLE_ITEMS))
        name = SAMPLE_ITEMS[position]
        names.append(name if lap == 0 else f"{name} {lap + 1}")
    return names


async def classify_with_stored_groups(db, item_names: list[str]) -> tuple[list[str], dict[str, str]]:
    """The stored group names, in order, and each item mapped to its group."""
    group_names = [group["name"] for group in await list_stored_groups(db)]
    if not group_names:
        return [], {}
    return group_names, await classifier.classify(item_names, group_names)


def format_assignments(group_names: list[str], assignments: dict[str, str]) -> list[str]:
    """One line per non-empty group, in store order with "Other" last."""
    lines = []
    for group in [*group_names, OTHER_GROUP_NAME]:
        items = [name for name, assigned in assignments.items() if assigned == group]
        if items:
            lines.append(f"{group} ({len(items)}): {', '.join(items)}")
    return lines


async def _classify_items(item_names: list[str]) -> tuple[list[str], dict[str, str]]:
    client = AsyncIOMotorClient(settings.mongo_uri)
    try:
        return await classify_with_stored_groups(client[settings.mongo_db], item_names)
    finally:
        client.close()


@app.command("toggle-user-approved")
def toggle_user_approved(
    email: str = typer.Argument(..., help="User email address."),
):
    approved = asyncio.run(_toggle_user_approved(email=email))
    if approved is None:
        typer.echo(f"User not found for email: {email}")
        raise typer.Exit(code=1)

    typer.echo(f"Toggled approved={approved} for email: {email}")


@app.command("set-user-admin")
def set_user_admin(
    email: str = typer.Argument(..., help="User email address."),
):
    admin = asyncio.run(_set_user_admin(email=email, is_admin=True))
    if admin is None:
        typer.echo(f"User not found for email: {email}")
        raise typer.Exit(code=1)

    typer.echo(f"Set admin={admin} for email: {email}")


@app.command("unset-user-admin")
def unset_user_admin(
    email: str = typer.Argument(..., help="User email address."),
):
    admin = asyncio.run(_set_user_admin(email=email, is_admin=False))
    if admin is None:
        typer.echo(f"User not found for email: {email}")
        raise typer.Exit(code=1)

    typer.echo(f"Set admin={admin} for email: {email}")


@app.command("seed-default-groups")
def seed_default_groups_command():
    inserted = asyncio.run(_seed_default_groups())
    if inserted is None:
        typer.echo("Groups already exist; nothing inserted.")
        raise typer.Exit(code=1)

    typer.echo(f"Inserted {inserted} default groups.")


@app.command("classify-items")
def classify_items_command(
    items: Optional[list[str]] = typer.Argument(None, help="Item names to classify."),
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="Read item names from a file, one per line."
    ),
    count: int = typer.Option(
        len(SAMPLE_ITEMS), "--count", "-n", min=1, help="Sample items to use when none are given."
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="Override MODEL_PROVIDER for this run (bedrock or mistral)."
    ),
):
    """Sends items to the real model with the stored groups and prints where each lands."""
    names = list(items or [])
    if file is not None:
        names += [line.strip() for line in file.read_text().splitlines() if line.strip()]
    if not names:
        names = sample_items(count)
    # The classifier answers per distinct name, so duplicates would only blur the counts.
    names = list(dict.fromkeys(names))
    if provider is not None:
        settings.model_provider = provider

    model_name = {"bedrock": settings.bedrock_model_id, "mistral": settings.mistral_model}.get(
        (settings.model_provider or "").strip().lower(), "?"
    )
    typer.echo(f"Provider: {settings.model_provider or '(not set)'}, model: {model_name}")
    typer.echo(f"Classifying {len(names)} items...")
    started = time.monotonic()
    try:
        group_names, assignments = asyncio.run(_classify_items(names))
    except ModelError as exc:
        typer.echo(f"Model call failed after {time.monotonic() - started:.1f}s: {exc}")
        raise typer.Exit(code=1)
    elapsed = time.monotonic() - started

    if not group_names:
        typer.echo("No groups are stored. Run `python -m app.tasks seed-default-groups` first.")
        raise typer.Exit(code=1)
    for line in format_assignments(group_names, assignments):
        typer.echo(line)
    other = sum(group == OTHER_GROUP_NAME for group in assignments.values())
    typer.echo(f"Done in {elapsed:.1f}s; {other} of {len(assignments)} items in {OTHER_GROUP_NAME}.")


if __name__ == "__main__":
    app()
