"""Shows a week as real dates; inside the system a week stays an ISO year and week number."""

from datetime import date, timedelta


def day_label(day: date) -> str:
    """'Nov 17, 2026'"""
    return f"{day:%b} {day.day}, {day.year}"


def week_dates(year: int, week: int) -> str:
    """'Nov 2 – 8, 2026', 'Oct 26 – Nov 1, 2026' or 'Dec 28, 2026 – Jan 3, 2027'."""
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    start = f"{monday:%b} {monday.day}"
    if monday.year != sunday.year:
        return f"{start}, {monday.year} – {sunday:%b} {sunday.day}, {sunday.year}"
    if monday.month != sunday.month:
        return f"{start} – {sunday:%b} {sunday.day}, {sunday.year}"
    return f"{start} – {sunday.day}, {sunday.year}"
