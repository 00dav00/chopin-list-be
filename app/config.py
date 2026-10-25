from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongo_uri: str = Field(
        alias="MONGO_URI",
    )
    mongo_db: str = Field(
        alias="MONGO_DB",
    )
    google_client_id: str = Field(
        alias="GOOGLE_CLIENT_ID",
    )
    chopin_list_fe_url: str = Field(
        alias="CHOPIN_LIST_FE_URL",
    )

    # Admin-notification email. Optional-absent rather than required: Settings()
    # is instantiated at module scope below, so a required field breaks every
    # import including tests/conftest.py. `None` means "not configured", not an
    # invented fallback; the send path logs loudly when these are absent.
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int | None = Field(default=None, alias="SMTP_PORT")
    # "implicit" | "starttls" | "none". Stated rather than inferred from the
    # port: see app/notifications.py TLS_MODES. Absent is a misconfiguration
    # outside dry-run, not a mode -- the send path refuses and says so.
    smtp_tls: str | None = Field(default=None, alias="SMTP_TLS")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    # Secrets are SecretStr so the settings repr, or a traceback that captures
    # it, never prints them.
    smtp_password: SecretStr | None = Field(default=None, alias="SMTP_PASSWORD")
    mail_from: str | None = Field(default=None, alias="MAIL_FROM")
    mail_dry_run: bool = Field(default=False, alias="MAIL_DRY_RUN")
    # Present and non-blank selects the Brevo HTTPS API over SMTP. It is not the
    # SMTP key: the two are separate Brevo credentials and neither works for the
    # other.
    brevo_api_key: SecretStr | None = Field(default=None, alias="BREVO_API_KEY")
    # Which model provider "Group by kind" uses: "bedrock" or "scaleway". Absent,
    # blank or unknown means grouping is unavailable; see app/llm.py.
    model_provider: str | None = Field(default=None, alias="MODEL_PROVIDER")
    # Bedrock credentials. Absent or blank means Bedrock is unavailable;
    # timeouts live in app/bedrock.py.
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: SecretStr | None = Field(
        default=None, alias="AWS_SECRET_ACCESS_KEY"
    )
    # Defaults requested explicitly. AWS lists this model's end of life as no
    # sooner than 2026-12-02.
    bedrock_model_id: str = Field(
        default="mistral.mistral-large-3-675b-instruct", alias="BEDROCK_MODEL_ID"
    )
    bedrock_region: str = Field(default="eu-west-2", alias="BEDROCK_REGION")
    # Scaleway Generative APIs. Either absent or blank means Scaleway is
    # unavailable; the URL and timeouts live in app/scaleway.py.
    scaleway_api_key: SecretStr | None = Field(default=None, alias="SCALEWAY_API_KEY")
    scaleway_model: str | None = Field(default=None, alias="SCALEWAY_MODEL")


settings = Settings()
