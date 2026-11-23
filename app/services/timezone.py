"""Conversions between stored UTC times and the school's local time.

Everything in the database is naive UTC. Admins type and read times in the
timezone from System Settings (default Asia/Jakarta), so the API converts at
the edge and sends both forms.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..models import SystemSetting

DEFAULT_TIMEZONE = "Asia/Jakarta"
UTC = ZoneInfo("UTC")


def local_tz():
    name = SystemSetting.get_value("locale.timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def to_utc(local_naive, tz=None):
    """Local wall-clock time (naive) -> naive UTC for storage."""
    if local_naive is None:
        return None
    return local_naive.replace(tzinfo=tz or local_tz()).astimezone(UTC).replace(tzinfo=None)


def to_local(utc_naive, tz=None):
    """Stored naive UTC -> naive local wall-clock time."""
    if utc_naive is None:
        return None
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(tz or local_tz()).replace(tzinfo=None)


def local_range_utc(first_day: date, last_day: date, tz=None):
    """[start of first_day, start of the day after last_day) in local time, as naive UTC."""
    tz = tz or local_tz()
    start = to_utc(datetime.combine(first_day, time.min), tz)
    end = to_utc(datetime.combine(last_day + timedelta(days=1), time.min), tz)
    return start, end


def local_today(tz=None):
    return datetime.now(tz or local_tz()).date()
