"""FastAPI 应用组装。"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.config import get_settings
from app.db.database import Base, engine
from app.api.auth import router as auth_router
from app.api.news import router as news_router
settings = get_settings(); app = FastAPI(title=settings.app_name, version="0.1.0"); app.include_router(auth_router); app.include_router(news_router)
@app.on_event("startup")
def startup() -> None:
    """开发模式创建缺失表并准备文件目录。"""
    settings.html_root.mkdir(parents=True, exist_ok=True); Base.metadata.create_all(engine)
@app.get("/healthz")
def health() -> dict:
    """返回服务状态。"""
    return {"status":"ok"}
@app.get("/")
def index():
    """提供前端页面。"""
    return FileResponse("frontend/index.html")
