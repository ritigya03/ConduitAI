from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://conduit:conduit@localhost:5432/conduitai"
    environment: str = "development"
    default_tenant_id: str = "demo-tenant"
    groq_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434/v1"


settings = Settings()
