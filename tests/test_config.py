from pydantic import SecretStr

from app.config import Settings

SENTINEL = "xkeysib-config-sentinel-0123456789"


def test_brevo_api_key_is_read_from_its_env_name(monkeypatch):
    # The only test that goes through env parsing: every other one patches the
    # attribute, so a typo in the alias would otherwise pass unnoticed.
    monkeypatch.setenv("BREVO_API_KEY", SENTINEL)

    assert Settings().brevo_api_key.get_secret_value() == SENTINEL


def test_brevo_api_key_absent_is_none(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)

    assert Settings().brevo_api_key is None


def test_brevo_api_key_empty_is_kept_as_empty_not_none(monkeypatch):
    # Blank-means-absent is decided in app/notifications.py, not here.
    monkeypatch.setenv("BREVO_API_KEY", "")

    assert Settings().brevo_api_key == SecretStr("")


def test_secrets_are_not_in_the_settings_repr(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", SENTINEL)
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-password-sentinel")

    text = repr(Settings())

    assert SENTINEL not in text
    assert "smtp-password-sentinel" not in text
