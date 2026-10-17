"""集中管理应用配置。"""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    """读取环境变量并提供开发默认值。"""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "DailyNews"
    database_url: str = "sqlite:///./dailynews.db"
    session_secret: str = "change-me-in-production"
    html_root: Path = Path("./data/html")
    cookie_secure: bool = False
    model_api_key: str | None = None
    milvus_uri: str | None = None
    smtp_host: str | None = None
@lru_cache
def get_settings() -> Settings:
    """返回缓存后的配置对象。"""
    return Settings()
