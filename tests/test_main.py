"""Application-level smoke tests against `app.main`.

This module deliberately does NOT import `app.config` or `settings`. The
allowed FE origin used here is a fixed sentinel pinned in `conftest.py` via
`os.environ.setdefault("CHOPIN_LIST_FE_URL", ...)` before `app.main` is
imported. Asserting against a literal — not against `settings.*` — keeps the
test a true contract test on `app/main.py`'s CORS middleware configuration
rather than a tautology that re-reads the same setting.
"""

import pytest

# Must match the value pinned in conftest.py's setdefault for CHOPIN_LIST_FE_URL.
KNOWN_FE_ORIGIN = "http://chopin-test-fe.invalid"


@pytest.mark.asyncio
async def test_cors_preflight_permits_authorization_header(client):
    response = await client.options(
        "/",
        headers={
            "Origin": KNOWN_FE_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == KNOWN_FE_ORIGIN

    allow_headers = response.headers.get("access-control-allow-headers", "")
    # Starlette's CORSMiddleware echoes the requested headers when allow_headers
    # is "*" or when the requested header is in the explicit allowlist. We
    # accept either form; the contract this test guards is "Authorization is
    # permitted", not the specific echo style.
    assert "authorization" in allow_headers.lower()
