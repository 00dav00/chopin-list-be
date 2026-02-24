# Shoplist API

FastAPI + MongoDB backend that authenticates every request with a Google ID token.

## Environment

- `MONGO_URI` (default `mongodb://localhost:27017`)
- `MONGO_DB` (default `shoplist`)
- `GOOGLE_CLIENT_ID` (required)

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

## Data Schema

See [`docs/data-schema.md`](docs/data-schema.md) for collection fields, indexes, and relationships.
