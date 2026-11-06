"""How weeks are shown to people."""

import pytest

from rhu.weeks import week_dates


@pytest.mark.parametrize("year, week, expected", [
    (2026, 45, "Nov 2 – 8, 2026"),            # inside one month
    (2026, 44, "Oct 26 – Nov 1, 2026"),       # across two months
    (2026, 53, "Dec 28, 2026 – Jan 3, 2027"),  # across two years
    (2026, 1, "Dec 29, 2025 – Jan 4, 2026"),   # ISO week 1 can start in the year before
])
def test_week_dates(year, week, expected):
    assert week_dates(year, week) == expected
