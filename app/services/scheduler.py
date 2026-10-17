"""每日和一次性任务的持久化调度辅助。"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.db.news_models import ScheduledTask

def next_run(frequency: str, run_at: datetime, timezone_name: str, now: datetime | None = None) -> datetime:
    """按任务时区计算下一次 UTC 执行时间，仅支持 daily/once。"""
    if frequency not in {"daily", "once"}: raise ValueError("仅支持 daily 或 once")
    zone = ZoneInfo(timezone_name); current = now or datetime.now(timezone.utc); local = run_at.astimezone(zone)
    if frequency == "once": return run_at.astimezone(timezone.utc)
    candidate = local.replace(year=current.astimezone(zone).year, month=current.astimezone(zone).month, day=current.astimezone(zone).day)
    if candidate <= current.astimezone(zone): candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)
