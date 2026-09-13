from app.config import Settings


def test_settings_reads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@z:5432/db")
    settings = Settings()
    assert settings.database_url == "postgresql+psycopg://x:y@z:5432/db"


def test_settings_has_sane_defaults():
    settings = Settings()
    assert settings.default_tenant_id == "demo-tenant"
    assert settings.environment in {"development", "production", "test"}


def test_settings_reads_groq_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    settings = Settings()
    assert settings.groq_api_key == "test-key"


def test_settings_ollama_base_url_default():
    settings = Settings()
    assert settings.ollama_base_url == "http://localhost:11434/v1"
