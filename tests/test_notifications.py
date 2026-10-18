import logging

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from app import notifications


# ---------------------------------------------------------------------------
# New-user admin notification. The trigger half of this contract lives in
# tests/test_auth.py (AGENTS.md: one test file per module).
# ---------------------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    """A complete, non-dry-run SMTP configuration."""
    monkeypatch.setattr(notifications.settings, "mail_dry_run", False)
    monkeypatch.setattr(notifications.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notifications.settings, "smtp_port", 587)
    monkeypatch.setattr(notifications.settings, "smtp_tls", "starttls")
    monkeypatch.setattr(notifications.settings, "smtp_user", "relay-user")
    monkeypatch.setattr(
        notifications.settings, "smtp_password", SecretStr("relay-password")
    )
    monkeypatch.setattr(notifications.settings, "mail_from", "no-reply@example.com")
    monkeypatch.setattr(notifications.settings, "brevo_api_key", None)


@pytest_asyncio.fixture
async def admins(db):
    """Two admins, plus users the query must exclude."""
    # google_sub on every row: production carries a unique index on it, so
    # documents without one could not coexist there.
    await db.users.insert_many(
        [
            {"google_sub": "sub-other", "email": "other@example.com", "admin": True},
            {"google_sub": "sub-admin", "email": "admin@example.com", "admin": True},
            {
                "google_sub": "sub-regular",
                "email": "regular@example.com",
                "admin": False,
            },
            {"google_sub": "sub-no-flag", "email": "no-flag@example.com"},
            {"google_sub": "sub-no-email", "admin": True},
        ]
    )
    return db


@pytest.fixture
def sent(monkeypatch):
    """Capture calls to the SMTP layer instead of opening a connection."""
    calls = []

    async def fake_send(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(notifications.aiosmtplib, "send", fake_send)
    return calls


@pytest.mark.asyncio
async def test_sends_to_every_admin_with_name_email_and_fe_deep_link(
    configured, sent, admins
):
    await notifications.send_new_user_notification(
        admins, "New Person", "new@example.com"
    )

    assert len(sent) == 1
    message, _ = sent[0]
    # Admins only, sorted; non-admins and an admin without an email are absent.
    assert message["To"] == "admin@example.com, other@example.com"
    assert message["From"] == "no-reply@example.com"

    body = message.get_content()
    assert "New Person" in body
    assert "new@example.com" in body
    # The FE route, hash-routed -- not the backend API path.
    assert "/#/admin/pending-users" in body
    # No internal vocabulary or identifiers in what a human reads (#8).
    assert "google_sub" not in body
    assert "403" not in body


@pytest.mark.asyncio
async def test_dry_run_writes_to_stdout_and_never_opens_smtp(
    monkeypatch, capsys, admins
):
    # Deliberately no SMTP credentials set: dry-run must not require them.
    monkeypatch.setattr(notifications.settings, "mail_dry_run", True)
    monkeypatch.setattr(notifications.settings, "brevo_api_key", None)
    monkeypatch.setattr(notifications.settings, "smtp_host", None)
    monkeypatch.setattr(notifications.settings, "smtp_port", None)
    monkeypatch.setattr(notifications.settings, "mail_from", None)

    async def explode(*args, **kwargs):
        raise AssertionError("dry run must not reach the SMTP layer")

    monkeypatch.setattr(notifications.aiosmtplib, "send", explode)

    await notifications.send_new_user_notification(
        admins, "New Person", "new@example.com"
    )

    out = capsys.readouterr().out
    assert "MAIL_DRY_RUN" in out
    assert "New Person" in out
    assert "/#/admin/pending-users" in out


@pytest.mark.asyncio
async def test_missing_smtp_config_outside_dry_run_logs_loudly_and_does_not_send(
    monkeypatch, sent, caplog, admins
):
    monkeypatch.setattr(notifications.settings, "mail_dry_run", False)
    monkeypatch.setattr(notifications.settings, "smtp_host", None)
    monkeypatch.setattr(notifications.settings, "brevo_api_key", None)
    monkeypatch.setattr(notifications.settings, "smtp_port", None)
    monkeypatch.setattr(notifications.settings, "smtp_tls", None)
    monkeypatch.setattr(notifications.settings, "mail_from", None)

    with caplog.at_level(logging.ERROR):
        await notifications.send_new_user_notification(
            admins, "New Person", "new@example.com"
        )

    assert sent == []
    # A production misconfiguration must be loud, and must name what is absent
    # -- a silent no-send is indistinguishable from "nobody signed up".
    assert "SMTP_HOST" in caplog.text
    assert "MAIL_FROM" in caplog.text
    # Absent SMTP_TLS is a misconfiguration, not an unstated default: a deploy
    # that forgets it must be told, not quietly given a guessed mode.
    assert "SMTP_TLS" in caplog.text
    # ...and must read differently from a transient relay failure. One means
    # "fix the deploy", the other means "ignore, the next signup retries".
    assert "Admin notification failed" not in caplog.text
    # Reaching this line at all is the assertion that it never raises: if this
    # check ever migrates into the request path, a raise here would turn the
    # pending-approval 403 into a 500.


@pytest.mark.asyncio
async def test_smtp_failure_is_swallowed_and_logged(
    configured, monkeypatch, caplog, admins
):
    async def explode(*args, **kwargs):
        raise ConnectionRefusedError("relay is down")

    monkeypatch.setattr(notifications.aiosmtplib, "send", explode)

    with caplog.at_level(logging.ERROR):
        # Must not raise: email is notification, not invariant.
        await notifications.send_new_user_notification(
            admins, "New Person", "new@example.com"
        )

    assert "Admin notification failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expect_use_tls,expect_start_tls",
    [
        ("implicit", True, False),
        ("starttls", False, True),
        # Plaintext is a real, reachable mode -- a local fake relay offers no
        # TLS, and an assumed STARTTLS against one fails outright.
        ("none", False, False),
    ],
)
async def test_tls_mode_comes_from_config_not_the_port(
    configured, sent, monkeypatch, admins, mode, expect_use_tls, expect_start_tls
):
    monkeypatch.setattr(notifications.settings, "smtp_tls", mode)
    # Pinned to a port whose convention contradicts every mode under test, so a
    # regression back to port-derived TLS fails here instead of passing by luck.
    monkeypatch.setattr(notifications.settings, "smtp_port", 2525)

    await notifications.send_new_user_notification(
        admins, "New Person", "new@example.com"
    )

    _, kwargs = sent[0]
    assert kwargs["use_tls"] is expect_use_tls
    assert kwargs["start_tls"] is expect_start_tls


@pytest.mark.asyncio
async def test_tls_mode_is_case_and_whitespace_insensitive(
    configured, sent, monkeypatch, admins
):
    monkeypatch.setattr(notifications.settings, "smtp_tls", "  STARTTLS ")

    await notifications.send_new_user_notification(
        admins, "New Person", "new@example.com"
    )

    _, kwargs = sent[0]
    assert kwargs["start_tls"] is True


@pytest.mark.asyncio
async def test_unrecognized_tls_mode_logs_loudly_and_does_not_send(
    configured, sent, monkeypatch, caplog, admins
):
    monkeypatch.setattr(notifications.settings, "smtp_tls", "ssl")

    with caplog.at_level(logging.ERROR):
        await notifications.send_new_user_notification(
            admins, "New Person", "new@example.com"
        )

    assert sent == []
    # Must name the offending value and the valid set: a typo is fixed by
    # editing config, so guessing a mode would hide the one actionable fact.
    assert "SMTP_TLS" in caplog.text
    assert "'ssl'" in caplog.text
    assert "starttls" in caplog.text
    # Not a transient relay failure -- retrying never fixes a typo.
    assert "Admin notification failed" not in caplog.text


@pytest.mark.asyncio
async def test_dispatch_returns_immediately_and_keeps_a_strong_task_reference(
    configured, sent, admins
):
    notifications.dispatch_new_user_notification(
        admins, "New Person", "new@example.com"
    )

    # Without the module-level strong reference the loop holds only a weak one
    # and the task can be collected before it runs.
    pending = set(notifications._pending_sends)
    assert pending

    for task in pending:
        await task
    assert len(sent) == 1
    # The done-callback must clear the set, or it leaks for the process life.
    assert not notifications._pending_sends


@pytest.mark.asyncio
async def test_no_admin_in_the_database_logs_loudly_and_does_not_send(
    configured, sent, db, caplog
):
    await db.users.insert_one({"email": "regular@example.com", "admin": False})

    with caplog.at_level(logging.ERROR):
        await notifications.send_new_user_notification(
            db, "New Person", "new@example.com"
        )

    assert sent == []
    # Must name the fix. This is reachable in production -- an admin who is
    # demoted or deleted leaves nobody to notify, and the app cannot tell
    # that apart from "nobody signed up" unless it says so.
    assert "admin=true" in caplog.text
    assert "set-user-admin" in caplog.text
    assert "Admin notification failed" not in caplog.text


# ---------------------------------------------------------------------------
# Brevo API transport. Selected when BREVO_API_KEY is set; the auth-path half
# of this contract lives in tests/test_auth.py.
# ---------------------------------------------------------------------------

# Distinctive so a leak into any log record is unmistakable.
KEY = "xkeysib-SENTINEL-do-not-log-0123456789"
SENT = "Admin notification sent"
TRANSIENT = "Admin notification failed"


@pytest.fixture
def api_configured(monkeypatch):
    """The production shape: a key and MAIL_FROM, no SMTP settings at all."""
    monkeypatch.setattr(notifications.settings, "mail_dry_run", False)
    monkeypatch.setattr(notifications.settings, "brevo_api_key", SecretStr(KEY))
    monkeypatch.setattr(notifications.settings, "mail_from", "no-reply@example.com")
    for name in ("smtp_host", "smtp_port", "smtp_tls", "smtp_user", "smtp_password"):
        monkeypatch.setattr(notifications.settings, name, None)


async def notify(db):
    await notifications.send_new_user_notification(
        db, "New Person", "new@example.com"
    )


def assert_key_never_logged(caplog):
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY not in (record.exc_text or "")
    assert KEY not in caplog.text


@pytest.mark.asyncio
async def test_outbound_http_guard_blocks_real_requests():
    # Proves the conftest guard is armed: a test that forgets to stub the
    # network fails loudly instead of sending live mail.
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="real outbound HTTP"):
            await client.get("https://api.brevo.com/v3/account")


# --- Transport selection ---------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_key_smtp_sends_and_the_api_is_untouched(
    configured, sent, brevo_api, admins
):
    await notify(admins)

    assert len(sent) == 1
    assert brevo_api.requests == []


@pytest.mark.asyncio
async def test_key_with_no_smtp_settings_sends_through_the_api(
    api_configured, sent, brevo_api, caplog, admins
):
    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert len(brevo_api.requests) == 1
    assert sent == []
    # SMTP settings are irrelevant on this path and must not be reported.
    assert "SMTP" not in caplog.text
    assert "Admin notification sent to 2 recipient(s) via brevo-api." in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("smtp_tls", ["starttls", "ssl"])
async def test_key_wins_over_complete_smtp_even_with_a_bad_tls_mode(
    configured, sent, brevo_api, monkeypatch, caplog, admins, smtp_tls
):
    monkeypatch.setattr(notifications.settings, "brevo_api_key", SecretStr(KEY))
    monkeypatch.setattr(notifications.settings, "smtp_tls", smtp_tls)

    with caplog.at_level(logging.ERROR):
        await notify(admins)

    assert len(brevo_api.requests) == 1
    assert sent == []
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_key_with_partial_smtp_does_not_report_smtp_gaps(
    api_configured, brevo_api, monkeypatch, caplog, admins
):
    monkeypatch.setattr(notifications.settings, "smtp_host", "smtp.example.com")

    with caplog.at_level(logging.ERROR):
        await notify(admins)

    assert len(brevo_api.requests) == 1
    assert caplog.text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   \n"])
async def test_blank_key_counts_as_absent(
    api_configured, brevo_api, monkeypatch, caplog, admins, blank
):
    monkeypatch.setattr(notifications.settings, "brevo_api_key", SecretStr(blank))

    with caplog.at_level(logging.ERROR):
        await notify(admins)

    # Falls back to SMTP, which is unconfigured, and says so.
    assert brevo_api.requests == []
    assert "SMTP_HOST" in caplog.text


@pytest.mark.asyncio
async def test_key_is_stripped_before_use(
    api_configured, brevo_api, monkeypatch, admins
):
    monkeypatch.setattr(
        notifications.settings, "brevo_api_key", SecretStr(f"  {KEY}\n")
    )

    await notify(admins)

    assert brevo_api.requests[0].headers["api-key"] == KEY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_key", [f"{KEY[:10]}\n{KEY[10:]}", f"{KEY}é", f"{KEY[:10]} {KEY[10:]}"]
)
async def test_malformed_key_is_refused_without_quoting_it(
    api_configured, brevo_api, monkeypatch, caplog, admins, bad_key
):
    monkeypatch.setattr(notifications.settings, "brevo_api_key", SecretStr(bad_key))

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert brevo_api.requests == []
    assert "BREVO_API_KEY" in caplog.text
    assert SENT not in caplog.text
    assert TRANSIENT not in caplog.text
    for part in bad_key.split():
        assert part not in caplog.text


@pytest.mark.asyncio
async def test_key_without_mail_from_names_only_mail_from(
    api_configured, brevo_api, monkeypatch, caplog, admins
):
    monkeypatch.setattr(notifications.settings, "mail_from", None)

    with caplog.at_level(logging.ERROR):
        await notify(admins)

    assert brevo_api.requests == []
    assert "MAIL_FROM" in caplog.text
    assert "SMTP" not in caplog.text
    assert TRANSIENT not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,smtp_host,transport",
    [(KEY, "smtp.example.com", "brevo-api"), (None, None, "smtp")],
)
async def test_dry_run_names_the_transport_and_never_sends(
    monkeypatch, capsys, brevo_api, sent, admins, key, smtp_host, transport
):
    monkeypatch.setattr(notifications.settings, "mail_dry_run", True)
    monkeypatch.setattr(
        notifications.settings, "brevo_api_key", SecretStr(key) if key else None
    )
    monkeypatch.setattr(notifications.settings, "smtp_host", smtp_host)

    await notify(admins)

    out = capsys.readouterr().out
    assert f"(transport: {transport})" in out
    assert KEY[:12] not in out
    assert brevo_api.requests == []
    assert sent == []


# --- What is sent ----------------------------------------------------------


@pytest.mark.asyncio
async def test_request_carries_key_header_and_the_smtp_message_content(
    api_configured, brevo_api, admins
):
    await notify(admins)

    request = brevo_api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.brevo.com/v3/smtp/email"
    assert request.headers["api-key"] == KEY
    assert request.headers["content-type"] == "application/json"

    # Same subject and body the SMTP path sends, so the two cannot drift.
    message = notifications._build_message(
        ["admin@example.com", "other@example.com"], "New Person", "new@example.com"
    )
    assert brevo_api.payload() == {
        "sender": {"email": "no-reply@example.com"},
        "to": [{"email": "admin@example.com"}, {"email": "other@example.com"}],
        "subject": "New Chopin List access request",
        "textContent": message.get_content(),
    }


@pytest.mark.asyncio
async def test_recipients_come_from_the_list_not_the_folded_to_header(
    api_configured, brevo_api, db
):
    emails = sorted(f"administrator.number.{i}@long-example-domain.com" for i in range(5))
    await db.users.insert_many(
        [{"google_sub": f"sub-{i}", "email": e, "admin": True} for i, e in enumerate(emails)]
    )

    await notify(db)

    # Guard against this test silently going vacuous: the header must fold.
    headers = notifications._build_message(emails, "N", "n@example.com").as_string()
    assert any(line.startswith((" ", "\t")) for line in headers.split("\n\n")[0].splitlines())
    assert brevo_api.payload()["to"] == [{"email": e} for e in emails]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mail_from,sender",
    [
        ("no-reply@example.com", {"email": "no-reply@example.com"}),
        (
            "Chopin List <no-reply@example.com>",
            {"email": "no-reply@example.com", "name": "Chopin List"},
        ),
    ],
)
async def test_sender_is_parsed_from_mail_from(
    api_configured, brevo_api, monkeypatch, admins, mail_from, sender
):
    monkeypatch.setattr(notifications.settings, "mail_from", mail_from)

    await notify(admins)

    assert brevo_api.payload()["sender"] == sender


# --- Responses ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(201, json={"messageId": "<id@smtp-relay.mailin.fr>"}),
        # Success must not depend on parsing the body.
        httpx.Response(201),
        httpx.Response(200, json={}),
    ],
)
async def test_any_2xx_is_success(api_configured, brevo_api, caplog, admins, response):
    brevo_api.respond = lambda request: response

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert "sent to 2 recipient(s) via brevo-api" in caplog.text
    assert_key_never_logged(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {"code": "unauthorized", "message": "Key not found"}, "BREVO_API_KEY"),
        (402, {"code": "not_enough_credits", "message": "x"}, "sending credits"),
        (400, {"code": "invalid_parameter", "message": "admin@example.com"}, "MAIL_FROM"),
        (403, {"code": "permission_denied", "message": "x"}, "Retrying will not help"),
        (404, {"code": "not_found", "message": "x"}, "Retrying will not help"),
        (406, {"code": "not_acceptable", "message": "x"}, "Retrying will not help"),
    ],
)
async def test_permanent_rejections_say_what_to_fix(
    api_configured, brevo_api, caplog, admins, status, body, expected
):
    brevo_api.respond = lambda request: httpx.Response(status, json=body)

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert expected in caplog.text
    assert repr(body["code"]) in caplog.text
    # `message` can quote admin addresses; only `code` is logged.
    assert body["message"] not in caplog.text or body["message"] == "x"
    assert "admin@example.com" not in caplog.text
    assert SENT not in caplog.text
    assert TRANSIENT not in caplog.text


@pytest.mark.asyncio
async def test_server_error_is_transient_and_names_no_setting(
    api_configured, brevo_api, caplog, admins
):
    brevo_api.respond = lambda request: httpx.Response(503, json={"code": "unavailable"})

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert TRANSIENT in caplog.text
    assert "HTTP 503" in caplog.text
    assert "BREVO_API_KEY" not in caplog.text
    assert "MAIL_FROM" not in caplog.text
    assert SENT not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            502,
            headers={"content-type": "text/html"},
            text=f"<html>bad gateway, request had api-key: {KEY}</html>",
        ),
        httpx.Response(500),
    ],
)
async def test_non_json_error_body_is_described_never_quoted(
    api_configured, brevo_api, caplog, admins, response
):
    brevo_api.respond = lambda request: response

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert TRANSIENT in caplog.text
    assert "bytes" in caplog.text
    assert "bad gateway" not in caplog.text
    assert SENT not in caplog.text
    assert_key_never_logged(caplog)


@pytest.mark.asyncio
async def test_redirect_is_not_followed(api_configured, brevo_api, caplog, admins):
    # Following it would forward the api-key header to whatever host it names.
    brevo_api.respond = lambda request: httpx.Response(
        307, headers={"location": "https://attacker.example/steal"}
    )

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        await notify(admins)

    assert len(brevo_api.requests) == 1
    assert SENT not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout("timed out"), RuntimeError("unexpected")],
)
async def test_network_errors_are_swallowed_as_transient(
    api_configured, brevo_api, caplog, admins, error
):
    def fail(request):
        raise error

    brevo_api.respond = fail

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        # Must not raise: email is notification, not invariant.
        await notify(admins)

    assert TRANSIENT in caplog.text
    assert SENT not in caplog.text
    assert_key_never_logged(caplog)


@pytest.mark.asyncio
async def test_client_is_hardened(api_configured, brevo_api, monkeypatch, admins):
    created = []
    real_client = httpx.AsyncClient

    def spy(**kwargs):
        created.append(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(notifications.httpx, "AsyncClient", spy)

    await notify(admins)

    (kwargs,) = created
    assert kwargs["verify"] is notifications.BREVO_SSL_CONTEXT
    assert kwargs["follow_redirects"] is False
    assert kwargs["trust_env"] is False
    assert kwargs["timeout"] == notifications.BREVO_TIMEOUT_SECONDS
