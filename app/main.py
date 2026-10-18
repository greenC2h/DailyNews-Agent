"""FastAPI 应用组装。"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.config import get_settings
from app.api.auth import router as auth_router
from app.api.news import router as news_router
from app.api.mail import router as mail_router
from app.api.tasks import router as tasks_router
from app.api.history import router as history_router
from app.api.chat import router as chat_router
settings = get_settings(); app = FastAPI(title=settings.app_name, version="0.1.0"); app.include_router(auth_router); app.include_router(news_router); app.include_router(mail_router); app.include_router(tasks_router); app.include_router(history_router); app.include_router(chat_router)
frontend_root = Path(__file__).resolve().parents[1] / "frontend" / "dist"
app.mount("/assets", StaticFiles(directory=frontend_root / "assets", check_dir=False), name="frontend-assets")
@app.on_event("startup")
def startup() -> None:
    """准备受控简报目录；数据库结构由 Alembic 管理。"""
    settings.html_root.mkdir(parents=True, exist_ok=True)
    from app.services.assistant_runtime import setup_persistence
    setup_persistence()
@app.get("/healthz")
def health() -> dict:
    """返回服务状态。"""
    return {"status":"ok"}
@app.get("/")
def index():
    """提供 Vite 构建页面，确保资源地址与生产静态挂载一致。"""
    return FileResponse(frontend_root / "index.html")
