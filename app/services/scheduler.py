"""每日和一次性任务的持久化调度辅助。"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.db.news_models import ScheduledTask
from app.db.models import User
from sqlalchemy import select
from sqlalchemy.orm import Session


def sync_default_schedule(db: Session, user: User) -> None:
    """在用户设置事务内创建或更新唯一默认每日任务，清空时间只暂停。

    Args:
        db: 已锁定用户设置的业务会话，不在本函数提交。
        user: 更新后的用户偏好。
    """
    task = db.scalar(select(ScheduledTask).where(ScheduledTask.user_id == user.id, ScheduledTask.is_default.is_(True)).with_for_update())
    if not user.daily_push_time:
        if task:
            task.status = "paused"
        return
    hour, minute = map(int, user.daily_push_time.split(":"))
    local = datetime.now(ZoneInfo(user.timezone)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not task:
        task = ScheduledTask(user_id=user.id, name="默认每日简报", frequency="daily", is_default=True, recipient_email=user.email)
        db.add(task)
    task.timezone = user.timezone
    task.run_at = local
    task.query = " ".join(user.topics + user.keywords) or "人工智能"
    task.limit = user.style.get("max_events", 5)
    task.status = "active"
    task.next_run_at = next_run("daily", local, user.timezone)

def previous_calendar_day(planned_at: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    """返回计划执行日所在时区的昨天自然日 UTC 半开区间。

    Args:
        planned_at: 带时区的计划执行时间，排队延迟不改变窗口。
        timezone_name: 任务保存的 IANA 时区。

    Returns:
        昨天零点与今天零点；夏令时切换日可能相差 23 或 25 小时。

    Raises:
        ValueError: 计划时间缺少时区。
    """
    if planned_at.tzinfo is None:
        raise ValueError("计划执行时间必须带时区")
    zone = ZoneInfo(timezone_name)
    local_end = planned_at.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    local_start = local_end - timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)

def next_run(frequency: str, run_at: datetime, timezone_name: str, now: datetime | None = None) -> datetime:
    """按任务时区计算下一次 UTC 执行时间，仅支持 daily/once。"""
    if frequency not in {"daily", "once"}: raise ValueError("仅支持 daily 或 once")
    zone = ZoneInfo(timezone_name); current = now or datetime.now(timezone.utc); local = run_at.astimezone(zone)
    if frequency == "once": return run_at.astimezone(timezone.utc)
    candidate = local.replace(year=current.astimezone(zone).year, month=current.astimezone(zone).month, day=current.astimezone(zone).day)
    if candidate <= current.astimezone(zone): candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)
