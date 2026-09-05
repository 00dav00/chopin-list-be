"""Admin notification email for new access requests.

Failure stance: **email is notification, not invariant.** Nothing in this module
may change the outcome of the request that triggered it. A new user's first
authenticated request always terminates in ``403 Account pending approval`` by
design; turning that into a 5xx because an SMTP relay hiccuped would be hostile,
so every failure here is logged and swallowed.

Dispatch is deliberately ``asyncio.create_task`` and **not** FastAPI's
``BackgroundTasks``. Background tasks are attached to a response after the
endpoint returns, and are dropped when a dependency raises. Authentication runs
as a dependency (``app.auth.get_current_user``) and the first-time-user path
always raises, so a ``BackgroundTasks`` send would never fire for exactly the
case this module exists to serve -- and would fail only in production, since a
test that patches the send at module level passes either way.

Provider-agnostic by construction: any SMTP relay (Brevo, SMTP2GO, SES, Gmail)
works through the same env vars. The one provider-shaped difference, TLS mode,
is stated per environment in ``SMTP_TLS`` rather than inferred from the port.
Naming the mode leaves the code just as vendor-blind as deriving it did, and it
lets a local fake relay that speaks no TLS at all be configured rather than
special-cased.
"""

import asyncio
import logging
from email.message import EmailMessage
from typing import Optional

import aiosmtplib

from .config import settings

logger = logging.getLogger(__name__)

# Tuning knobs are module constants, not env config (team convention #5); env is
# for secrets and endpoints. The timeout matters: aiosmtplib's default is long
# enough that a black-holed port would pin the task for the better part of a
# minute.
SMTP_TIMEOUT_SECONDS = 10.0

# SMTP_TLS -> (use_tls, start_tls) for aiosmtplib. Previously derived from the
# port (465 implicit, everything else STARTTLS), which guessed wrong for the two
# cases that matter: a relay on a nonstandard port, and a local fake relay
# offering no TLS at all -- against which an assumed STARTTLS fails with
# "STARTTLS extension not supported by server".
#
# "none" is plaintext and belongs only to local development. Do not pair it with
# SMTP_USER/SMTP_PASSWORD: those credentials would cross the wire in the clear.
TLS_MODES = {
    "implicit": (True, False),  # TLS from the first byte; conventionally 465
    "starttls": (False, True),  # plaintext connect, then upgrade; 587 / 2525
    "none": (False, False),  # no encryption; local fake relays only
}

# Hash-based routing on the frontend (svelte-spa-router), so the emailed link
# must carry the "#". This is the FE route, NOT the backend API path.
PENDING_USERS_FRAGMENT = "/#/admin/pending-users"

# asyncio holds only a weak reference to a running task. Without a strong
# reference the task can be garbage-collected mid-flight and the email silently
# never sends.
_pending_sends: set[asyncio.Task] = set()


def _recipients() -> list[str]:
    """Admin recipients, parsed from the comma-separated env value."""
    return [
        address.strip()
        for address in (settings.mail_admin_to or "").split(",")
        if address.strip()
    ]


def _pending_users_link() -> str:
    return f"{settings.chopin_list_fe_url.rstrip('/')}{PENDING_USERS_FRAGMENT}"


def _build_message(name: Optional[str], email: Optional[str]) -> EmailMessage:
    """Plain-text notification. No IDs, tokens, or internal vocabulary (#8)."""
    display_name = name or "Someone"
    message = EmailMessage()
    message["Subject"] = "New Chopin List access request"
    message["From"] = settings.mail_from or ""
    message["To"] = ", ".join(_recipients())
    message.set_content(
        f"{display_name} has requested access to Chopin List.\n"
        f"\n"
        f"Name: {display_name}\n"
        f"Email: {email or 'unknown'}\n"
        f"\n"
        f"Review and approve access here:\n"
        f"{_pending_users_link()}\n"
    )
    return message


def _missing_config() -> list[str]:
    """Names of the settings required to actually send, that are absent."""
    required = {
        "SMTP_HOST": settings.smtp_host,
        "SMTP_PORT": settings.smtp_port,
        "SMTP_TLS": settings.smtp_tls,
        "MAIL_FROM": settings.mail_from,
        "MAIL_ADMIN_TO": settings.mail_admin_to,
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
        password=settings.smtp_password or None,
        use_tls=use_tls,
        start_tls=start_tls,
        timeout=SMTP_TIMEOUT_SECONDS,
    )


async def send_new_user_notification(
    name: Optional[str], email: Optional[str]
) -> None:
    """Compose and send the admin notification. Never raises."""
    try:
        recipients = _recipients()
        if not recipients and not settings.mail_dry_run:
            logger.error(
                "Admin notification NOT sent: MAIL_ADMIN_TO is not configured. "
                "New users will request access with nobody notified."
            )
            return

        message = _build_message(name, email)

        if settings.mail_dry_run:
            # Dry run writes to stdout so a developer running the app with no
            # credentials can see exactly what would have been delivered.
            print("--- MAIL_DRY_RUN: admin notification not sent ---")
            print(message)
            return

        missing = _missing_config()
        if missing:
            # A production misconfiguration must be loud. Because the SMTP
            # settings are optional-absent, the alternative is a silent
            # no-send that looks identical to "nobody signed up."
            logger.error(
                "Admin notification NOT sent: missing SMTP configuration (%s). "
                "Set these or enable MAIL_DRY_RUN for local development.",
                ", ".join(missing),
            )
            return

        tls_mode = _tls_mode()
        if tls_mode is None:
            # Present but unrecognized. Distinct from absent, and distinct from
            # a relay failure: neither retrying nor waiting fixes a typo.
            logger.error(
                "Admin notification NOT sent: SMTP_TLS=%r is not one of %s.",
                settings.smtp_tls,
                ", ".join(sorted(TLS_MODES)),
            )
            return

        await _deliver(message, tls_mode)
        logger.info("Admin notification sent to %d recipient(s).", len(recipients))
    except Exception:
        logger.exception(
            "Admin notification failed to send. Sign-in was not affected."
        )


def dispatch_new_user_notification(
    name: Optional[str], email: Optional[str]
) -> None:
    """Fire-and-forget the notification. Returns immediately; never raises.

    Callers are on the auth path and must not be delayed or failed by this.
    """
    try:
        task = asyncio.create_task(send_new_user_notification(name, email))
    except RuntimeError:
        # No running event loop (e.g. a synchronous call site). Nothing to do
        # here that wouldn't block the caller, which is the one thing this
        # module promises not to do.
        logger.exception("Admin notification could not be scheduled.")
        return
    _pending_sends.add(task)
    task.add_done_callback(_pending_sends.discard)
