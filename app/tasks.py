import asyncio

import typer
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from .config import settings
from .routers.groups import name_key
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
            "name_key": name_key(name),
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


if __name__ == "__main__":
    app()
