"""Admin notification emails: new access requests and unmatched grocery items.

Email is notification, not invariant: every failure here is logged and
swallowed so it can never change the outcome of the request that triggered it.
Sends through Brevo's HTTPS API when ``BREVO_API_KEY`` is set, else plain SMTP.
"""

import asyncio
import json
import logging
import re
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Callable, Coroutine, Optional

import aiosmtplib
import httpx

from .config import settings

logger = logging.getLogger(__name__)

# Tuning knobs are module constants, not env config (team convention #5); env is
# for secrets and endpoints. The timeout matters: aiosmtplib's default is long
# enough that a black-holed port would pin the task for the better part of a
# minute.
SMTP_TIMEOUT_SECONDS = 10.0

# Where the API key is sent, so never configurable from env or request data.
BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
# httpx timeouts are per phase, and a server trickling bytes resets the read
# timeout on every chunk; the send is also wrapped in this as a total bound.
BREVO_TIMEOUT_SECONDS = 10.0
# Built once: each new client would otherwise re-parse the CA bundle,
# synchronously, on the event loop.
BREVO_SSL_CONTEXT = httpx.create_ssl_context()
# Error bodies are read only this far. Brevo's `message` quotes the offending
# value (admin addresses), so only the short `code` is ever logged.
BREVO_ERROR_BODY_LIMIT = 4096
BREVO_ERROR_CODE = re.compile(r"[a-z_]{1,64}")
# Retrying never fixes these; 401 is a bad key or an IP not authorised in Brevo.
BREVO_PERMANENT_STATUSES = {400, 401, 403, 404, 406}

TRANSPORT_API = "brevo-api"
TRANSPORT_SMTP = "smtp"

# SMTP_TLS -> (use_tls, start_tls) for aiosmtplib. Named, not derived from the
# port: deriving guessed wrong for a relay on a nonstandard port and for a local
# fake relay offering no TLS at all. "none" is plaintext and local-only -- never
# pair it with SMTP_USER/SMTP_PASSWORD, which would cross the wire in the clear.
TLS_MODES = {
    "implicit": (True, False),  # TLS from the first byte; conventionally 465
    "starttls": (False, True),  # plaintext connect, then upgrade; 587 / 2525
    "none": (False, False),  # no encryption; local fake relays only
}

# Hash-based routing on the frontend (svelte-spa-router), so the emailed link
# must carry the "#". This is the FE route, NOT the backend API path.
PENDING_USERS_FRAGMENT = "/#/admin/pending-users"
UNMATCHED_ITEMS_FRAGMENT = "/#/admin/unmatched-items"
UNMATCHED_ITEMS_SUBJECT = "Chopin List items that fit no group"

# asyncio holds only a weak reference to a running task. Without a strong
# reference the task can be garbage-collected mid-flight and the email silently
# never sends.
_pending_sends: set[asyncio.Task] = set()


async def _recipients(db) -> list[str]:
    """Every user flagged `admin`, sorted so the To header is stable.

    Not filtered by `approved`: the first admin is created by
    `python -m app.tasks set-user-admin`, which sets the flag alone.
    """
    cursor = db.users.find({"admin": True}, {"email": 1}).sort("email", 1)
    return [doc["email"] async for doc in cursor if doc.get("email")]


def _frontend_link(fragment: str) -> str:
    return f"{settings.chopin_list_fe_url.rstrip('/')}{fragment}"


def _pending_users_link() -> str:
    return _frontend_link(PENDING_USERS_FRAGMENT)


def _build_email(recipients: list[str], subject: str, body: str) -> EmailMessage:
    """Plain text only. Subjects are fixed literals, never user text."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.mail_from or ""
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    return message


def _build_message(
    recipients: list[str], name: Optional[str], email: Optional[str]
) -> EmailMessage:
    """Plain-text notification. No IDs, tokens, or internal vocabulary (#8)."""
    display_name = name or "Someone"
    return _build_email(
        recipients,
        "New Chopin List access request",
        f"{display_name} has requested access to Chopin List.\n"
        f"\n"
        f"Name: {display_name}\n"
        f"Email: {email or 'unknown'}\n"
        f"\n"
        f"Review and approve access here:\n"
        f"{_pending_users_link()}\n",
    )


def _build_unmatched_items_message(
    recipients: list[str], names: list[str]
) -> EmailMessage:
    # Names are shopper text, already cleaned of line breaks by the caller, so
    # each stays on its own line and cannot forge extra lines in the body.
    listing = "\n".join(f"- {name}" for name in names)
    return _build_email(
        recipients,
        UNMATCHED_ITEMS_SUBJECT,
        "A shopper chose to share items that fit none of the current groups:\n"
        f"\n{listing}\n"
        "\n"
        "Review them and add groups here:\n"
        f"{_frontend_link(UNMATCHED_ITEMS_FRAGMENT)}\n",
    )


def _brevo_api_key() -> Optional[str]:
    """The key with surrounding whitespace stripped, or None if absent or blank."""
    secret = settings.brevo_api_key
    key = secret.get_secret_value().strip() if secret else ""
    return key or None


def _is_valid_api_key(key: str) -> bool:
    # Printable ASCII only. A control character inside the key reaches h11,
    # which quotes the header value in its exception -- and that gets logged.
    return all("!" <= char <= "~" for char in key)


def _missing_config(transport: str) -> list[str]:
    """Names of the settings the chosen transport needs to send, that are absent.

    MAIL_FROM is checked with the SMTP settings, not before them, so one message
    names every gap.
    """
    required = {"MAIL_FROM": settings.mail_from}
    if transport == TRANSPORT_SMTP:
        required |= {
            "SMTP_HOST": settings.smtp_host,
            "SMTP_PORT": settings.smtp_port,
            "SMTP_TLS": settings.smtp_tls,
        }
    return sorted(name for name, value in required.items() if not value)


def _tls_mode() -> Optional[str]:
    """The configured mode, normalized, or None if it names no known mode."""
    mode = (settings.smtp_tls or "").strip().lower()
    return mode if mode in TLS_MODES else None


async def _deliver(message: EmailMessage, tls_mode: str) -> None:
    use_tls, start_tls = TLS_MODES[tls_mode]
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=(
            settings.smtp_password.get_secret_value()
            if settings.smtp_password
            else None
        ),
        use_tls=use_tls,
        start_tls=start_tls,
        timeout=SMTP_TIMEOUT_SECONDS,
    )


def _brevo_payload(recipients: list[str], message: EmailMessage) -> dict:
    # Recipients come from the list, not the To header, which EmailMessage folds
    # with newlines once it is long. Keys are fixed: nothing from the database
    # or the token is spread into this.
    sender_name, sender_email = parseaddr(settings.mail_from or "")
    sender = {"email": sender_email}
    if sender_name:
        sender["name"] = sender_name
    return {
        "sender": sender,
        "to": [{"email": recipient} for recipient in recipients],
        "subject": str(message["Subject"]),
        "textContent": message.get_content(),
    }


def _brevo_error_detail(response: httpx.Response) -> str:
    """Brevo's error `code`, or a description of a body that is not Brevo's JSON.

    Never the body text itself: a CDN or proxy page can echo request headers.
    """
    try:
        body = json.loads(response.content[:BREVO_ERROR_BODY_LIMIT])
    except ValueError:
        body = None
    code = body.get("code") if isinstance(body, dict) else None
    if isinstance(code, str) and BREVO_ERROR_CODE.fullmatch(code):
        return f"code {code!r}"
    return "body: %s, %d bytes" % (
        response.headers.get("content-type", "no content type"),
        len(response.content),
    )


def _log_brevo_failure(response: httpx.Response) -> None:
    status = response.status_code
    detail = _brevo_error_detail(response)
    if status == 401:
        logger.error(
            "Admin notification NOT sent: Brevo refused the credentials "
            "(HTTP 401, %s). Check BREVO_API_KEY is an API key, not the SMTP "
            "key, and that this server's IP is authorised in Brevo.",
            detail,
        )
    elif status == 402 and detail == "code 'not_enough_credits'":
        logger.error(
            "Admin notification NOT sent: the Brevo account is out of sending "
            "credits (HTTP 402, %s); the free plan has a daily cap.",
            detail,
        )
    elif status in BREVO_PERMANENT_STATUSES:
        logger.error(
            "Admin notification NOT sent: Brevo rejected the request (HTTP %d, "
            "%s). Retrying will not help; check MAIL_FROM is a sender verified "
            "in Brevo.",
            status,
            detail,
        )
    else:
        logger.error(
            "Admin notification failed to send: Brevo returned HTTP %d (%s). "
            "Sign-in was not affected.",
            status,
            detail,
        )


async def _deliver_api(
    api_key: str, recipients: list[str], message: EmailMessage
) -> bool:
    """POST to Brevo. True once Brevo accepts it; False after logging why not.

    Accepted is not delivered: Brevo answers 201 for an unverified sender and
    drops the mail afterwards, visible only in its own logs.
    """
    if not _is_valid_api_key(api_key):
        logger.error(
            "Admin notification NOT sent: BREVO_API_KEY contains spaces, control "
            "or non-ASCII characters. Re-paste it; the value is not logged."
        )
        return False

    # Each setting is stated, not inherited: verification on, no redirects (they
    # would forward the api-key header to the new host), no proxy env vars.
    async with asyncio.timeout(BREVO_TIMEOUT_SECONDS):
        async with httpx.AsyncClient(
            verify=BREVO_SSL_CONTEXT,
            follow_redirects=False,
            trust_env=False,
            timeout=BREVO_TIMEOUT_SECONDS,
        ) as client:
            response = await client.post(
                BREVO_SEND_URL,
                headers={"api-key": api_key, "accept": "application/json"},
                json=_brevo_payload(recipients, message),
            )

    if response.is_success:
        return True
    _log_brevo_failure(response)
    return False


async def _send_admin_email(
    db, build: Callable[[list[str]], EmailMessage]
) -> None:
    """Compose with `build(recipients)` and send to every admin. Never raises."""
    try:
        recipients = await _recipients(db)
        if not recipients:
            logger.error(
                "Admin notification NOT sent: no user has admin=true, so there "
                "is nobody to notify. Grant it with "
                "`python -m app.tasks set-user-admin <email>`."
            )
            return

        message = build(recipients)
        # Chosen before the dry-run return so dry-run can say which would run.
        api_key = _brevo_api_key()
        transport = TRANSPORT_API if api_key else TRANSPORT_SMTP

        if settings.mail_dry_run:
            # Dry run writes to stdout so a developer running the app with no
            # credentials can see exactly what would have been delivered.
            print(
                "--- MAIL_DRY_RUN: admin notification not sent "
                f"(transport: {transport}) ---"
            )
            print(message)
            return

        missing = _missing_config(transport)
        if missing:
            # A production misconfiguration must be loud. Because the mail
            # settings are optional-absent, the alternative is a silent
            # no-send that looks identical to "nobody signed up."
            logger.error(
                "Admin notification NOT sent: missing %s configuration (%s). "
                "Set these or enable MAIL_DRY_RUN for local development.",
                "mail" if transport == TRANSPORT_API else "SMTP",
                ", ".join(missing),
            )
            return

        if api_key:
            if not await _deliver_api(api_key, recipients, message):
                return
        else:
            tls_mode = _tls_mode()
            if tls_mode is None:
                # Present but unrecognized. Distinct from absent, and distinct
                # from a relay failure: neither retrying nor waiting fixes a typo.
                logger.error(
                    "Admin notification NOT sent: SMTP_TLS=%r is not one of %s.",
                    settings.smtp_tls,
                    ", ".join(sorted(TLS_MODES)),
                )
                return
            await _deliver(message, tls_mode)

        logger.info(
            "Admin notification sent to %d recipient(s) via %s.",
            len(recipients),
            transport,
        )
    except Exception:
        logger.exception(
            "Admin notification failed to send. The request that triggered it "
            "was not affected."
        )


async def send_new_user_notification(
    db, name: Optional[str], email: Optional[str]
) -> None:
    await _send_admin_email(
        db, lambda recipients: _build_message(recipients, name, email)
    )


async def send_unmatched_items_notification(db, names: list[str]) -> None:
    await _send_admin_email(
        db, lambda recipients: _build_unmatched_items_message(recipients, names)
    )


def _dispatch(send: Coroutine[Any, Any, None]) -> None:
    """Fire-and-forget `send`. Returns immediately; never raises."""
    try:
        task = asyncio.create_task(send)
    except RuntimeError:
        # No running event loop (e.g. a synchronous call site). Nothing to do
        # here that wouldn't block the caller, which is the one thing this
        # module promises not to do.
        send.close()
        logger.exception("Admin notification could not be scheduled.")
        return
    _pending_sends.add(task)
    task.add_done_callback(_pending_sends.discard)


def dispatch_unmatched_items_notification(db, names: list[str]) -> None:
    """Fire-and-forget; the send request has already recorded the names."""
    _dispatch(send_unmatched_items_notification(db, names))


def dispatch_new_user_notification(
    db, name: Optional[str], email: Optional[str]
) -> None:
    """Fire-and-forget the notification. Returns immediately; never raises.

    Callers are on the auth path and must not be delayed or failed by this.
    """
    # Not BackgroundTasks: those attach to a response and are dropped when a
    # dependency raises. Auth runs as a dependency and a first-time user's
    # request always raises 403, so a BackgroundTasks send would never fire for
    # the one case this exists to serve -- and would fail only in production.
    _dispatch(send_new_user_notification(db, name, email))
