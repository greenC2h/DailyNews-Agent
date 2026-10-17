"""创建独立 PostgreSQL 验收库并启动本地验收 Web，不接触业务表。"""
import os
from pathlib import Path
import subprocess
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import get_settings


def main():
    """幂等创建专用验收库，迁移后启动 8000 端口。"""
    settings = get_settings()
    url = make_url(settings.database_url)
    name = 'dailynews_acceptance_20261018'
    admin = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        if not connection.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'), {'name': name}):
            connection.execute(text('CREATE DATABASE dailynews_acceptance_20261018'))
    admin.dispose()
    os.environ['DATABASE_URL'] = url.set(database=name).render_as_string(hide_password=False)
    os.environ['HTML_ROOT'] = str(Path('data/acceptance/html').resolve())
    subprocess.run(['uv', 'run', 'alembic', 'upgrade', 'head'], check=True, env=os.environ)
    subprocess.run(['uv', 'run', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'], check=True, env=os.environ)

if __name__ == '__main__':
    main()
