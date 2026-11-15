"""Demo scenarios: how each astronaut's physiology deviates on given days.

Pattern: Strategy + Registry. Each ``Scenario`` returns the ``Modifiers`` of a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from core.escenarios import CIRCADIAN, WatchChange, infection

# =====================================================================
# 3. SCENARIOS (demo cases)
# =====================================================================

@dataclass(frozen=True)
class Modifiers:
    """Physiological changes a scenario applies on a given day."""

    watch: WatchChange = WatchChange()
    sleep_h: float | None = None
    blunted_car: bool = False
    high_night_cortisol: bool = False
    weight_loss_per_day: float = 0.0004   # ~2 % in 50 days (baseline in flight)
    muscle_loss_pct_per_day: float = 0.01
    hyperglycemia: bool = False


class Scenario:
    """Base scenario: healthy astronaut."""

    name: ClassVar[str] = "none"

    def modifiers(self, day: int) -> Modifiers:
        return Modifiers()


class InfectionScenario(Scenario):
    """Fever with tachycardia, low HRV and fast breathing on days 30-33."""

    name = "infection"
    DAYS = range(30, 34)

    def modifiers(self, day: int) -> Modifiers:
        if day not in self.DAYS:
            return Modifiers()
        return Modifiers(watch=infection(0.6 + 0.1 * (day - self.DAYS.start)))   # 0.6 -> 0.9 °C


class CircadianScenario(Scenario):
    """Short sleep, blunted CAR and high night cortisol on days 20-27."""

    name = "circadian"
    DAYS = range(20, 28)

    def modifiers(self, day: int) -> Modifiers:
        if day not in self.DAYS:
            return Modifiers()
        return Modifiers(watch=CIRCADIAN, sleep_h=4.6, blunted_car=True, high_night_cortisol=True)


class AtrophyScenario(Scenario):
    """Faster mass and muscle loss during the whole mission (~8 % mass in 50 days)."""

    name = "atrophy"

    def modifiers(self, day: int) -> Modifiers:
        return Modifiers(weight_loss_per_day=0.0016, muscle_loss_pct_per_day=0.06)


class GlycemicScenario(Scenario):
    """One post-meal hyperglycemia on day 15."""

    name = "glycemic"

    def modifiers(self, day: int) -> Modifiers:
        return Modifiers(hyperglycemia=day == 15)


SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in (Scenario(), InfectionScenario(), CircadianScenario(),
                        AtrophyScenario(), GlycemicScenario())
}


