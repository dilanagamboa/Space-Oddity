"""Mission protocol: calendar, sleep window and sampling plan.

Single source of truth for *when* things happen in a mission. The data generator,
the reminders service and the daily report pipeline all read these values, so the
schedule of a measurement is defined once (team document, "Frecuencia de medición").

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

HourMinute = tuple[int, int]


# =====================================================================
# 1. MISSION CALENDAR (day 1 = start date)
# =====================================================================

def mission_date(start: date | str, mission_day: int) -> date:
    """Calendar date of a mission day."""
    first = date.fromisoformat(str(start)[:10]) if not isinstance(start, date) else start
    return first + timedelta(days=mission_day - 1)


def mission_moment(start: date | str, mission_day: int, at: HourMinute | time) -> datetime:
    """Naive UTC moment of a mission day at a given hour (how the database stores it)."""
    clock = at if isinstance(at, time) else time(*at)
    return datetime.combine(mission_date(start, mission_day), clock)


def weekday(mission_day: int) -> int:
    """Position of a mission day in the weekly sampling cycle (0-6)."""
    return mission_day % 7


# Days of the week chosen with the L-D buttons, stored as a bit mask (L = bit 0 ... D = bit 6)
WEEKDAY_LABELS = ("L", "K", "M", "J", "V", "S", "D")
EVERY_DAY = 0b1111111


def weekdays_mask(days: Iterable[int]) -> int:
    """Bit mask of weekday indexes (0 = Monday)."""
    return sum(1 << d for d in set(days))


def weekdays_of(mask: int) -> list[int]:
    """Weekday indexes (0 = Monday) in a bit mask."""
    return [d for d in range(7) if mask >> d & 1]


def runs_on(mask: int, day: date) -> bool:
    """``True`` if a schedule with that mask happens on ``day``."""
    return bool(mask >> day.weekday() & 1)


# =====================================================================
# 2. SLEEP WINDOW
# =====================================================================

@dataclass(frozen=True)
class SleepWindow:
    """Scheduled sleep window in UTC hours (22:00-06:00, 8 h)."""

    start_hour: int = 22
    end_hour: int = 6

    def contains(self, hour: int) -> bool:
        if self.start_hour > self.end_hour:          # Crosses midnight
            return hour >= self.start_hour or hour < self.end_hour
        return self.start_hour <= hour < self.end_hour

    @property
    def hours(self) -> int:
        return (self.end_hour - self.start_hour) % 24


SLEEP_WINDOW = SleepWindow()


# =====================================================================
# 3. WEEKLY SAMPLING PLAN (standard monitoring mode)
# =====================================================================

GLUCOSE_WEEKDAYS: tuple[int, ...] = (1, 4)    # 2 days per week, 4 readings each
SALIVA_WEEKDAY: int = 3                         # 1 day per week, 4 samples
CHAIR_WEEKDAY: int = 1                          # Weekly fasting bioimpedance
HEMOGLOBIN_WEEKDAY: int = 0                     # Weekly hemoglobin
REST_WEEKDAY: int = 0                           # No scheduled exercise

GLUCOSE_FASTING_AT: HourMinute = (6, 30)
GLUCOSE_POST_MEAL_AT: tuple[HourMinute, ...] = ((9, 30), (14, 30), (20, 30))
SALIVA_AT: dict[str, HourMinute] = {             # collection_slot -> time
    "wake_0m": (6, 0),
    "wake_30m": (6, 30),
    "afternoon": (16, 0),
    "bedtime": (21, 45),
}
CHAIR_AT: HourMinute = (6, 15)
HEMOGLOBIN_AT: HourMinute = (6, 20)
AEROBIC_EXERCISE_HOUR: int = 8
RESISTANCE_EXERCISE_HOUR: int = 15
