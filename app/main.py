"""FastAPI 应用组装。"""
from uuid import uuid4

from fastapi import FastAPI, Request
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.config import get_settings
from app.db.database import engine
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


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """为每个 HTTP 响应设置请求追踪编号，包括流式响应。"""
    request_id = request.headers.get("X-Request-ID", "")[:100].strip()
    if not request_id or not all(char.isascii() and (char.isalnum() or char in "-_.:") for char in request_id):
        request_id = str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/healthz")
def health() -> JSONResponse:
    """检查关键 PostgreSQL 依赖并返回服务就绪状态。"""
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
@app.get("/")
def index():
    """提供 Vite 构建页面，确保资源地址与生产静态挂载一致。"""
    return FileResponse(frontend_root / "index.html")
