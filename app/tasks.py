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


async def _toggle_user_approved(email: str) -> bool | None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    try:
        db = client[settings.mongo_db]
        return await toggle_user_approved_by_email(db=db, email=email)
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


if __name__ == "__main__":
    app()
