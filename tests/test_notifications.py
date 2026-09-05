import logging

import pytest

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
    monkeypatch.setattr(notifications.settings, "smtp_password", "relay-password")
    monkeypatch.setattr(notifications.settings, "mail_from", "no-reply@example.com")
    monkeypatch.setattr(
        notifications.settings, "mail_admin_to", "admin@example.com, other@example.com"
    )


@pytest.fixture
def sent(monkeypatch):
    """Capture calls to the SMTP layer instead of opening a connection."""
    calls = []

    async def fake_send(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(notifications.aiosmtplib, "send", fake_send)
    return calls


@pytest.mark.asyncio
async def test_sends_to_all_recipients_with_name_email_and_fe_deep_link(
    configured, sent
):
    await notifications.send_new_user_notification("New Person", "new@example.com")

    assert len(sent) == 1
    message, _ = sent[0]
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
async def test_dry_run_writes_to_stdout_and_never_opens_smtp(monkeypatch, capsys):
    # Deliberately no SMTP credentials set: dry-run must not require them.
    monkeypatch.setattr(notifications.settings, "mail_dry_run", True)
    monkeypatch.setattr(notifications.settings, "smtp_host", None)
    monkeypatch.setattr(notifications.settings, "smtp_port", None)
    monkeypatch.setattr(notifications.settings, "mail_from", None)
    monkeypatch.setattr(notifications.settings, "mail_admin_to", "admin@example.com")

    async def explode(*args, **kwargs):
        raise AssertionError("dry run must not reach the SMTP layer")

    monkeypatch.setattr(notifications.aiosmtplib, "send", explode)

    await notifications.send_new_user_notification("New Person", "new@example.com")

    out = capsys.readouterr().out
    assert "MAIL_DRY_RUN" in out
    assert "New Person" in out
    assert "/#/admin/pending-users" in out


@pytest.mark.asyncio
async def test_missing_smtp_config_outside_dry_run_logs_loudly_and_does_not_send(
    monkeypatch, sent, caplog
):
    monkeypatch.setattr(notifications.settings, "mail_dry_run", False)
    monkeypatch.setattr(notifications.settings, "smtp_host", None)
    monkeypatch.setattr(notifications.settings, "smtp_port", None)
    monkeypatch.setattr(notifications.settings, "smtp_tls", None)
    monkeypatch.setattr(notifications.settings, "mail_from", None)
    monkeypatch.setattr(notifications.settings, "mail_admin_to", "admin@example.com")

    with caplog.at_level(logging.ERROR):
        await notifications.send_new_user_notification("New Person", "new@example.com")

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
    # Reaching this line at all is the assertion that it never raises: on the
    # create_task path a raise is contained, but if this check ever migrates
    # into the request path it would turn the 403 into a 500 -- the exact
    # failure AC3 exists to prevent.


@pytest.mark.asyncio
async def test_smtp_failure_is_swallowed_and_logged(configured, monkeypatch, caplog):
    async def explode(*args, **kwargs):
        raise ConnectionRefusedError("relay is down")

    monkeypatch.setattr(notifications.aiosmtplib, "send", explode)

    with caplog.at_level(logging.ERROR):
        # Must not raise: email is notification, not invariant.
        await notifications.send_new_user_notification("New Person", "new@example.com")

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
    configured, sent, monkeypatch, mode, expect_use_tls, expect_start_tls
):
    monkeypatch.setattr(notifications.settings, "smtp_tls", mode)
    # Pinned to a port whose convention contradicts every mode under test, so a
    # regression back to port-derived TLS fails here instead of passing by luck.
    monkeypatch.setattr(notifications.settings, "smtp_port", 2525)

    await notifications.send_new_user_notification("New Person", "new@example.com")

    _, kwargs = sent[0]
    assert kwargs["use_tls"] is expect_use_tls
    assert kwargs["start_tls"] is expect_start_tls


@pytest.mark.asyncio
async def test_tls_mode_is_case_and_whitespace_insensitive(
    configured, sent, monkeypatch
):
    monkeypatch.setattr(notifications.settings, "smtp_tls", "  STARTTLS ")

    await notifications.send_new_user_notification("New Person", "new@example.com")

    _, kwargs = sent[0]
    assert kwargs["start_tls"] is True


@pytest.mark.asyncio
async def test_unrecognized_tls_mode_logs_loudly_and_does_not_send(
    configured, sent, monkeypatch, caplog
):
    monkeypatch.setattr(notifications.settings, "smtp_tls", "ssl")

    with caplog.at_level(logging.ERROR):
        await notifications.send_new_user_notification("New Person", "new@example.com")

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
    configured, sent
):
    notifications.dispatch_new_user_notification("New Person", "new@example.com")

    # Without the module-level strong reference the loop holds only a weak one
    # and the task can be collected before it runs.
    pending = set(notifications._pending_sends)
    assert pending

    for task in pending:
        await task
    assert len(sent) == 1
    # The done-callback must clear the set, or it leaks for the process life.
    assert not notifications._pending_sends
