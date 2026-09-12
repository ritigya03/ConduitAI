from app.config import Settings


def test_settings_reads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@z:5432/db")
    settings = Settings()
    assert settings.database_url == "postgresql+psycopg://x:y@z:5432/db"


def test_settings_has_sane_defaults():
    settings = Settings()
    assert settings.default_tenant_id == "demo-tenant"
    assert settings.environment in {"development", "production", "test"}
