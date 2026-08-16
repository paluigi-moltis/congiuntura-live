"""Tests for the calendar module (offline — parsing logic only)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from congiuntura_live.calendar.collectors import (
    ForexFactoryCollector,
    DestatisCollector,
    make_uid,
)


class TestForexFactoryParsing:
    def test_parse_datetime_regular(self):
        # 8:00am ET (EDT, UTC-4) = 12:00 UTC
        dt = ForexFactoryCollector._parse_datetime("Aug 14", "8:00am", 2026)
        assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 8, 14, 12)

    def test_parse_datetime_pm(self):
        dt = ForexFactoryCollector._parse_datetime("Aug 14", "4:00pm", 2026)
        assert dt.hour == 20

    def test_parse_datetime_tentative(self):
        dt = ForexFactoryCollector._parse_datetime("Aug 14", "Tentative", 2026)
        assert dt.day == 14
        assert dt.hour == 4  # midnight ET = 04:00 UTC

    def test_parse_datetime_12am(self):
        dt = ForexFactoryCollector._parse_datetime("Aug 14", "12:00am", 2026)
        assert dt.hour == 4

    def test_parse_datetime_12pm(self):
        dt = ForexFactoryCollector._parse_datetime("Aug 14", "12:00pm", 2026)
        assert dt.hour == 16

    def test_parse_datetime_winter_est(self):
        # January = EST (UTC-5): 8:00am ET = 13:00 UTC
        dt = ForexFactoryCollector._parse_datetime("Jan 15", "8:00am", 2027)
        assert dt.hour == 13

    def test_shift_month(self):
        assert ForexFactoryCollector._shift_month(2026, 12, 1) == (2027, 1)
        assert ForexFactoryCollector._shift_month(2026, 1, -1) == (2025, 12)

    def test_month_range_cross_year(self):
        months = ForexFactoryCollector._month_range(2026, 11, 2027, 2)
        assert months == [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]

    def test_routine_window_is_5_months(self):
        c = ForexFactoryCollector()
        now = datetime.now(UTC)
        sy, sm = c._shift_month(now.year, now.month, -c.LOOKBACK_MONTHS)
        ey, em = c._shift_month(now.year, now.month, c.LOOKAHEAD_MONTHS)
        assert len(c._month_range(sy, sm, ey, em)) == 5


class TestDestatisParsing:
    def test_parse_date_summer(self):
        # 08:00 Berlin (CEST, UTC+2) = 06:00 UTC
        dt = DestatisCollector._parse_cet_date("2026.08.14")
        assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 8, 14, 6)

    def test_parse_date_winter(self):
        # 08:00 Berlin (CET, UTC+1) = 07:00 UTC
        dt = DestatisCollector._parse_cet_date("2026.12.18")
        assert dt.hour == 7


class TestUID:
    def test_make_uid_stable(self):
        assert make_uid("istat", "CPI", "2026-08-14T10:00:00+00:00") == \
               make_uid("istat", "CPI", "2026-08-14T10:00:00+00:00")

    def test_make_uid_distinct(self):
        assert make_uid("istat", "CPI", "a") != make_uid("ine", "CPI", "a")
