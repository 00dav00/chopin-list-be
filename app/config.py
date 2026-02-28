from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongo_uri: str = Field(
        validation_alias="MONGO_URI",
    )
    mongo_db: str = Field(
        validation_alias="MONGO_DB",
    )
    google_client_id: str
    chopin_list_fe_url: str = Field(
        validation_alias="CHOPIN_LIST_FE_URL",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
