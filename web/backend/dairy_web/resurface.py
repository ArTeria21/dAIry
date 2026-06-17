from __future__ import annotations

import random
from collections.abc import Sequence

from dairy_web.data_access import DayRecord


def resurface_weight(day: DayRecord) -> float:
    base = 1.0 + max(0.0, min(1.0, day.mood_confidence))
    if day.mood != "neutral":
        base += 1.0
    return base


def choose_resurface_day(
    days: Sequence[DayRecord],
    *,
    rng: random.Random | None = None,
) -> DayRecord | None:
    if not days:
        return None
    random_source = rng or random
    weights = [resurface_weight(day) for day in days]
    return random_source.choices(list(days), weights=weights, k=1)[0]
