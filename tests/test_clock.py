from datetime import date, datetime

from daykeep.clock import logical_date, stamp


def test_before_rollover_belongs_to_previous_day():
    assert logical_date(datetime(2026, 10, 18, 3, 59), 4) == date(2026, 10, 17)


def test_at_rollover_belongs_to_new_day():
    assert logical_date(datetime(2026, 10, 18, 4, 0), 4) == date(2026, 10, 18)


def test_midday_belongs_to_same_day():
    assert logical_date(datetime(2026, 10, 18, 23, 50), 4) == date(2026, 10, 18)


def test_stamp_format():
    assert stamp(datetime(2026, 10, 18, 21, 30)) == "[21:30] "
