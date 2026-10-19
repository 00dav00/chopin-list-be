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


def test_aws_credentials_are_read_from_their_env_names(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-key-id-sentinel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-sentinel")

    loaded = Settings()

    assert loaded.aws_access_key_id == "aws-key-id-sentinel"
    assert loaded.aws_secret_access_key.get_secret_value() == "aws-secret-sentinel"
    assert "aws-secret-sentinel" not in repr(loaded)


def test_aws_credentials_absent_are_none(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    loaded = Settings()

    assert loaded.aws_access_key_id is None
    assert loaded.aws_secret_access_key is None


def test_bedrock_model_and_region_are_read_from_their_env_names(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-sentinel")
    monkeypatch.setenv("BEDROCK_REGION", "region-sentinel")

    loaded = Settings()

    assert loaded.bedrock_model_id == "model-sentinel"
    assert loaded.bedrock_region == "region-sentinel"


def test_bedrock_model_and_region_default_when_absent(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("BEDROCK_REGION", raising=False)

    loaded = Settings()

    assert loaded.bedrock_model_id == "mistral.mistral-large-3-675b-instruct"
    assert loaded.bedrock_region == "eu-west-2"
