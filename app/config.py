"""集中管理应用配置。"""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    """读取环境变量并提供开发默认值。"""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "DailyNews"
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "dailynews"
    db_user: str = "postgres"
    db_password: str = ""
    db_sslmode: str = "prefer"
    session_secret: str = "change-me-in-production"
    html_root: Path = Path("./data/html")
    cookie_secure: bool = False
    model_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    news_model_name: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int = 3072
    milvus_uri: str | None = None
    milvus_token: str | None = None
    milvus_collection: str = "dailynews_events"
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_from_name: str = "DailyNewsAgent"
    smtp_tls_mode: str = "ssl"
    open_sandbox_api_key: str | None = None
    open_sandbox_base_url: str | None = None
    open_sandbox_id: str | None = None
    open_sandbox_command_timeout: int = 60
@lru_cache
def get_settings() -> Settings:
    """返回缓存后的配置对象。"""
    settings = Settings()
    if not settings.database_url and settings.db_password:
        from urllib.parse import quote_plus
        settings.database_url = f"postgresql+psycopg://{quote_plus(settings.db_user)}:{quote_plus(settings.db_password)}@{settings.db_host}:{settings.db_port}/{settings.db_name}?sslmode={settings.db_sslmode}"
    if not settings.model_api_key:
        settings.model_api_key = settings.deepseek_api_key
    if not settings.deepseek_base_url:
        settings.deepseek_base_url = settings.model_base_url
    if settings.deepseek_base_url and not settings.deepseek_base_url.rstrip('/').endswith('/v1'):
        settings.deepseek_base_url = settings.deepseek_base_url.rstrip('/') + '/v1'
    if settings.embedding_base_url and not settings.embedding_base_url.rstrip('/').endswith('/v1'):
        settings.embedding_base_url = settings.embedding_base_url.rstrip('/') + '/v1'
    if not settings.milvus_uri:
        settings.milvus_uri = "http://localhost:19530"
    return settings
