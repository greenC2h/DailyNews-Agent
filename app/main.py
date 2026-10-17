"""FastAPI 应用组装。"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.config import get_settings
from app.api.auth import router as auth_router
from app.api.news import router as news_router
from app.api.mail import router as mail_router
from app.api.tasks import router as tasks_router
from app.api.history import router as history_router
from app.api.chat import router as chat_router
settings = get_settings(); app = FastAPI(title=settings.app_name, version="0.1.0"); app.include_router(auth_router); app.include_router(news_router); app.include_router(mail_router); app.include_router(tasks_router); app.include_router(history_router); app.include_router(chat_router)
@app.on_event("startup")
def startup() -> None:
    """准备受控简报目录；数据库结构由 Alembic 管理。"""
    settings.html_root.mkdir(parents=True, exist_ok=True)
@app.get("/healthz")
def health() -> dict:
    """返回服务状态。"""
    return {"status":"ok"}
@app.get("/")
def index():
    """提供前端页面。"""
    return FileResponse("frontend/index.html")
