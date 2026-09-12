from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://conduit:conduit@localhost:5432/conduitai"
    environment: str = "development"
    default_tenant_id: str = "demo-tenant"


settings = Settings()
