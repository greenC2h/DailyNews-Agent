"""数据库引擎、会话和 ORM 基类。"""
from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from app.config import get_settings
class Base(DeclarativeBase):
    """所有业务模型的声明式基类。"""
settings = get_settings(); connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True); SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
def get_db() -> Generator[Session, None, None]:
    """为请求提供数据库会话并在结束后释放资源。"""
    db = SessionLocal()
    try: yield db
    finally: db.close()
