from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://conduit:conduit@localhost:5432/conduitai"
    environment: str = "development"
    default_tenant_id: str = "demo-tenant"
    groq_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
    mock_crm_base_url: str = "http://localhost:8100"
    mock_crm_api_token: str = "mock-crm-demo-token"
    # Comma-separated. Deployed frontend origins (e.g. the Vercel URL) go
    # here via the CORS_ALLOWED_ORIGINS env var -- local dev's default
    # covers itself without any config needed.
    cors_allowed_origins: str = "http://localhost:3000"
    # ydata-profiling (pandas + matplotlib + scipy) OOM-crashes on Render's
    # free 512MB tier even in isolation, with nothing else loaded -- a real
    # OS-level kill, not something a try/except can catch. Off by default
    # on constrained deploys; on for local/Docker Compose, which has no
    # memory cap. Set ENABLE_HTML_REPORTS=true to force it on.
    enable_html_reports: bool = True

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


settings = Settings()
