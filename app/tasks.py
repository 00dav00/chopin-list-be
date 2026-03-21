import asyncio

import typer
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from .config import settings

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


if __name__ == "__main__":
    app()
