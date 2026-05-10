# Contributing

This guide covers how to validate changes locally before opening a pull request.

## Prerequisites

- Python `3.12` (defined in `.python-version`).
- A running MongoDB 7 reachable at `localhost:27017`. If you use Docker, `docker compose up -d mongo` from the repo root works; a local install (e.g. via Homebrew) listening on the same port works too.
- The `CHOPIN_LIST_FE_URL` environment variable must be exported before running tests. Any non-empty value works for the test suite — for example:

  ```bash
  export CHOPIN_LIST_FE_URL=http://localhost:5173
  ```

  This is required because `app/config.py` validates it at import time.

## Run tests

Create and activate a virtual environment, then install dependencies once:

```bash
python -m venv .venv
source .venv/bin/activate
make install
```

Then, before opening a PR, run:

```bash
make test
```

`make test` runs the same command CI runs: `pytest`. It must exit 0.

## Open a PR

Push your branch and open a pull request against `main`. The `Backend tests` GitHub Actions workflow gates on the same command `make test` runs — if `make test` is green locally, CI should be too.
