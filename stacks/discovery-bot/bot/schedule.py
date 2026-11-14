"""Saturday 10:00 local wall-clock schedule. All inputs/outputs are tz-aware."""
from datetime import datetime, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

RUN_WEEKDAY = 5  # Saturday
RUN_HOUR = 10
CATCH_UP_WINDOW = timedelta(days=3)


def _zone(tz: Union[str, ZoneInfo]) -> ZoneInfo:
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return dt


def _slot(day, zone: ZoneInfo) -> datetime:
    # Build from wall-clock fields so the offset is right on either side of a DST change.
    return datetime(day.year, day.month, day.day, RUN_HOUR, 0, tzinfo=zone)


def latest_run(now: datetime, tz) -> datetime:
    """Most recent Saturday 10:00 local at or before now."""
    zone = _zone(tz)
    local = _aware(now).astimezone(zone)
    day = local.date() - timedelta(days=(local.weekday() - RUN_WEEKDAY) % 7)
    slot = _slot(day, zone)
    if slot > now:
        slot = _slot(day - timedelta(days=7), zone)
    return slot


def next_run(now: datetime, tz) -> datetime:
    """First Saturday 10:00 local strictly after now."""
    zone = _zone(tz)
    prev = latest_run(now, zone)
    return _slot(prev.date() + timedelta(days=7), zone)


def should_catch_up(now: datetime, last_post_at: Optional[datetime], tz) -> bool:
    """True when the latest scheduled slot is unposted and still within 3 days."""
    slot = latest_run(now, tz)
    if now - slot > CATCH_UP_WINDOW:
        return False
    return last_post_at is None or _aware(last_post_at) < slot
