from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "shoplist"
    google_client_id: str
    chopin_list_fe_url: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
