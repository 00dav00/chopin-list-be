from pydantic import Field
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


settings = Settings()
