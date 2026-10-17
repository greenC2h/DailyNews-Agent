"""每日新闻自然日窗口的时区及夏令时回归测试。"""
from datetime import datetime, timedelta, timezone
import pytest
from app.services.scheduler import previous_calendar_day


@pytest.mark.parametrize("planned,zone,start,end,hours", [
    ("2026-10-18T12:00:00+00:00", "Asia/Shanghai", "2026-10-16T16:00:00+00:00", "2026-10-17T16:00:00+00:00", 24),
    ("2026-03-09T12:00:00+00:00", "America/New_York", "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00", 23),
    ("2026-11-02T12:00:00+00:00", "America/New_York", "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00", 25),
])
def test_previous_day_uses_local_midnights(planned, zone, start, end, hours):
    """验证窗口按本地零点计算，不是运行时向前滚动 24 小时。"""
    actual_start, actual_end = previous_calendar_day(datetime.fromisoformat(planned), zone)
    assert actual_start == datetime.fromisoformat(start)
    assert actual_end == datetime.fromisoformat(end)
    assert actual_end - actual_start == timedelta(hours=hours)


def test_naive_planned_time_is_rejected():
    """拒绝缺少时区的计划时间，避免使用宿主默认时区。"""
    with pytest.raises(ValueError):
        previous_calendar_day(datetime(2026, 10, 18), "Asia/Shanghai")
