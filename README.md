# Shoplist API

[![Backend tests](https://github.com/00dav00/chopin-list-be/actions/workflows/be.yml/badge.svg)](https://github.com/00dav00/chopin-list-be/actions/workflows/be.yml)

FastAPI + MongoDB backend that authenticates every request with a Google ID token.

## Environment

- `MONGO_URI` (required)
- `MONGO_DB` (required)
- `GOOGLE_CLIENT_ID` (required)
- `CHOPIN_LIST_FE_URL` (required)

Admins are emailed when a new user requests access. Recipients are every user
with `admin: true` in the database, so there is nothing to configure per admin
— grant the flag with `python -m app.tasks set-user-admin <email>`. If nobody
holds it, the app logs an error and sends nothing.

Mail goes out one of two ways, chosen on every send:

- **Brevo's HTTPS API**, when `BREVO_API_KEY` is set and not blank. Use this
  where outbound SMTP is blocked (Railway's Free, Trial and Hobby plans).
- **SMTP** otherwise, configured with:
  - `SMTP_HOST`, `SMTP_PORT`
  - `SMTP_TLS` — `implicit` (TLS from connect, conventionally port 465),
    `starttls` (upgrade after connect, conventionally 587/2525), or `none`
    (plaintext, local development only)
  - `SMTP_USER`, `SMTP_PASSWORD` — omit for a relay that needs no auth

Both use:

- `MAIL_FROM` — `addr@example.com` or `Name <addr@example.com>`
- `MAIL_DRY_RUN` — print the message, and which transport would have sent it,
  instead of sending

All of these are optional so the app boots without credentials, but a
deployment missing what its transport needs logs an error and sends nothing.
Set `SMTP_TLS` explicitly in every environment that sends over SMTP: it is not
inferred from the port.

Brevo gotchas, each found the hard way:

- The **API key and the SMTP key are different credentials**. The SMTP key in
  `BREVO_API_KEY` fails with a 401 that reads like a bad key.
- If the Brevo account restricts **authorised IPs**, the server's outbound IP
  must be on the list, or every send is a 401. Home IPv6 addresses rotate;
  allow the IPv4 address instead.
- `MAIL_FROM` must be a **sender verified in Brevo**. An unverified one is not
  refused: the API answers 201 and Brevo drops the mail afterwards, so the app
  logs a send that never arrives. Check Brevo's transactional logs.
- Brevo rewrites the envelope sender to a `brevosend.com` address unless the
  sender's domain is authenticated (DKIM/DMARC) in Brevo.

### Trying it locally

`docker compose up` starts [Mailpit](https://mailpit.axllent.org/), a fake
inbox that captures mail instead of delivering it. The defaults in `.env`
point at it; read what was sent at <http://localhost:8025>.

Make yourself an admin first, or there is nobody to send to:

```bash
docker compose run --rm api python -m app.tasks set-user-admin you@example.com
```

Only a user's *first* sign-in notifies, so to re-trigger it, drop the account
first:

```bash
docker compose exec mongo mongosh shoplist \
  --eval 'db.users.deleteOne({email:"you@example.com"})'
```

`docker compose up` never uses Brevo, even with `BREVO_API_KEY` in `.env`: the
override file blanks it, because a live key would mail real admins from your
laptop. To send real mail through Brevo on purpose, set `BREVO_API_KEY` and
`BREVO_MAIL_FROM` (a Brevo-verified sender) in `.env` and add the opt-in file:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.brevo.yml up
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
