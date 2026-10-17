# DailyNews

本项目仍处于实现和验收阶段，尚未达到全部基线要求。

## 本地启动

在仓库根目录配置本地 `.env`（参考 `.env.example`，不得提交密钥），执行：

```powershell
uv sync
uv run alembic upgrade head
npm --prefix frontend ci
npm --prefix frontend run build
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

另开终端运行 `uv run python -m app.worker`。访问 `http://127.0.0.1:8000/`。
前端开发执行 `npm --prefix frontend run dev`，Vite 将 `/api` 代理至本地 8000 端口。
构建目录不提交；FastAPI 提供 `frontend/dist/index.html` 和 `/assets` 资源。

## 验证

先构建前端，再运行 `uv run python -m pytest -q`。测试使用临时数据库和目录；外部模型、SMTP、Milvus 的真实验收另行记录于《实施进度.md》，自动化测试不能替代它们。

Milvus 只通过 `uv run python -m app.cli init-milvus` 显式初始化。修改 Embedding 模型或维度时使用新 collection 名，不删除或原地覆盖已有 collection。当前 Embedding 服务的模型授权阻塞及尚未实现内容见《实施进度.md》。
