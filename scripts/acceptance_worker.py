"""绑定隔离验收库运行 DailyNews 常驻 Worker。"""
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


def main() -> None:
    """设置验收数据库和 HTML 目录后启动常驻 Worker。"""
    settings = get_settings()
    os.environ["DATABASE_URL"] = make_url(settings.database_url).set(database="dailynews_acceptance_20261018").render_as_string(hide_password=False)
    os.environ["HTML_ROOT"] = "data/acceptance/html"
    get_settings.cache_clear()
    from app.worker import run_worker

    run_worker(0.5)


if __name__ == "__main__":
    main()
