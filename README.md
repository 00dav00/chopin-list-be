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

## Auth

Pass the Google ID token in the `Authorization` header:

```
Authorization: Bearer <google-id-token>
```
