# Shoplist API

[![Backend tests](https://github.com/00dav00/chopin-list-be/actions/workflows/be.yml/badge.svg)](https://github.com/00dav00/chopin-list-be/actions/workflows/be.yml)

FastAPI + MongoDB backend that authenticates every request with a Google ID token.

## Environment

- `MONGO_URI` (required)
- `MONGO_DB` (required)
- `GOOGLE_CLIENT_ID` (required)
- `CHOPIN_LIST_FE_URL` (required)

Admins are emailed when a new user requests access. The relay is configured
with:

- `SMTP_HOST`, `SMTP_PORT`
- `SMTP_TLS` — `implicit` (TLS from connect, conventionally port 465),
  `starttls` (upgrade after connect, conventionally 587/2525), or `none`
  (plaintext, local development only)
- `SMTP_USER`, `SMTP_PASSWORD` — omit for a relay that needs no auth
- `MAIL_FROM`, `MAIL_ADMIN_TO` (comma-separated)
- `MAIL_DRY_RUN` — print the message instead of sending it

These are optional so the app boots without credentials, but a deployment
missing any of them logs an error and sends nothing. Set `SMTP_TLS` explicitly
in every environment that sends: it is not inferred from the port.

### Trying it locally

`docker compose up` starts [Mailpit](https://mailpit.axllent.org/), a fake
inbox that captures mail instead of delivering it. The defaults in `.env`
point at it; read what was sent at <http://localhost:8025>.

Only a user's *first* sign-in notifies, so to re-trigger it, drop the account
first:

```bash
docker compose exec mongo mongosh shoplist \
  --eval 'db.users.deleteOne({email:"you@example.com"})'
```

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests

Tests use a separate MongoDB database name and expect a local Mongo instance.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export GOOGLE_CLIENT_ID=test-client-id
docker-compose up -d mongo
pytest -q
```

Set `TEST_DB_NAME` to pin the test database name if needed.

## Auth

Pass the Google ID token in the `Authorization` header:

```
Authorization: Bearer <google-id-token>
```

## Tasks

Manually approve or put a user account on hold by email:

```bash
python -m app.tasks toggle-user-approved user@example.com
```

Grant or remove admin role by email:

```bash
python -m app.tasks set-user-admin user@example.com
python -m app.tasks unset-user-admin user@example.com
```

## Data Schema

See [`docs/data-schema.md`](docs/data-schema.md) for collection fields, indexes, and relationships.

## Conditional GET (ETag / 304)

`GET /lists/{list_id}` supports `If-None-Match` for cheap polling. See [`docs/conditional-get.md`](docs/conditional-get.md) for the contract.

## Railway Deployment

See [`docs/railway-live-updates.md`](docs/railway-live-updates.md) for deployment notes.
