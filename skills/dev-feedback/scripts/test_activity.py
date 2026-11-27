#!/usr/bin/env python3
"""Самопроверка fetch_reviewer_activity.py: поиск перерывов в активности. Запуск: python3 test_activity.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_reviewer_activity import gaps  # noqa: E402

days = ["2025-11-20", "2025-11-26", "2026-01-23", "2026-01-24"]
assert gaps(days, 14) == [{"from": "2025-11-26", "to": "2026-01-23", "days": 58}]
assert gaps(["2026-06-01", "2026-06-15"], 14) == [], "ровно 14 дней — ещё не перерыв"
assert gaps([], 14) == [] and gaps(["2026-06-01"], 14) == []

print("ok")
