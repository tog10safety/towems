"""Philippine time and the 24-hour military format (spec §7)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from apps.core import timeutils


def test_datetime_format_is_philippine_military_time():
    value = dt.datetime(2026, 10, 29, 14, 30)
    assert timeutils.format_datetime(value) == "29 Oct 2026, 1430H (PST)"


def test_midnight_and_noon_use_24_hour_notation():
    assert timeutils.format_time(dt.datetime(2026, 10, 29, 0, 0)) == "0000H"
    assert timeutils.format_time(dt.datetime(2026, 10, 29, 12, 0)) == "1200H"
    assert timeutils.format_time(dt.datetime(2026, 10, 29, 23, 59)) == "2359H"


def test_single_digit_hours_and_days_are_padded():
    value = dt.datetime(2026, 1, 5, 6, 5)
    assert timeutils.format_date(value) == "05 Jan 2026"
    assert timeutils.format_time(value) == "0605H"


def test_aware_utc_value_is_converted_to_manila():
    # 06:30 UTC is 14:30 in the Philippines (UTC+08:00, no DST).
    utc = dt.datetime(2026, 10, 29, 6, 30, tzinfo=dt.timezone.utc)
    assert timeutils.format_datetime(utc) == "29 Oct 2026, 1430H (PST)"
    assert timeutils.to_manila(utc) == dt.datetime(2026, 10, 29, 14, 30)


def test_naive_values_are_already_philippine_time():
    """Naive == stored Manila wall clock; converting again would shift it."""
    naive = dt.datetime(2026, 10, 29, 14, 30)
    assert timeutils.to_manila(naive) == naive


def test_a_date_crossing_midnight_stays_on_its_philippine_day():
    # 16:30 UTC is 00:30 the next day in Manila.
    utc = dt.datetime(2026, 10, 29, 16, 30, tzinfo=dt.timezone.utc)
    assert timeutils.format_datetime(utc) == "30 Oct 2026, 0030H (PST)"


def test_now_is_naive_philippine_time_regardless_of_host_zone():
    value = timeutils.now()
    assert value.tzinfo is None
    expected = dt.datetime.now(ZoneInfo("Asia/Manila")).replace(tzinfo=None)
    assert abs((value - expected).total_seconds()) < 60


def test_today_matches_the_philippine_calendar_date():
    assert timeutils.today() == timeutils.now().date()


def test_missing_values_format_as_empty_strings():
    assert timeutils.format_datetime(None) == ""
    assert timeutils.format_date(None) == ""
    assert timeutils.format_time(None) == ""
