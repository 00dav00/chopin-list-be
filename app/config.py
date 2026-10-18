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


settings = Settings()
