#!/usr/bin/env python3
"""Самопроверка нетривиальной части fetch_timing.py: разбор дат, рабочие часы,
фильтр апрувов, подсчёт волн. Запуск: python3 test_timing.py"""

import sys
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_timing import APPROVAL_RE, calendar_hours, parse_ts, segment, work_hours  # noqa: E402

MSK = timezone(timedelta(hours=3))

# оба формата GitLab: ноты с миллисекундами в UTC, коммиты со смещением
note = parse_ts("2026-08-21T10:38:20.993Z", MSK)
commit = parse_ts("2026-08-21T14:00:01.000+03:00", MSK)
assert note is not None and commit is not None
assert commit > note, "смешанные форматы должны сравниваться разбором"
assert calendar_hours(note, commit) == 0.4, calendar_hours(note, commit)
assert parse_ts("", MSK) is None

# рабочие часы: пятница 18:00 → понедельник 11:00 = 1 ч пт + 1 ч пн, суббота/воскресенье не в счёт
fri = parse_ts("2026-08-21T18:00:00+03:00", MSK)
mon = parse_ts("2026-08-24T11:00:00+03:00", MSK)
assert work_hours(fri, mon) == 2.0, work_hours(fri, mon)
assert calendar_hours(fri, mon) == 65.0, calendar_hours(fri, mon)
# целый рабочий день внутри окна
assert work_hours(parse_ts("2026-08-24T00:00:00+03:00", MSK), parse_ts("2026-08-25T00:00:00+03:00", MSK)) == 9.0
assert work_hours(mon, fri) == 0.0, "обратный интервал = 0"

# апрувы не должны попадать в замечания
for ok in ("ок", "Ок.", "аудит ок", "Аудит ок!", "кросс-аудит ок", "кросс ок"):
    assert APPROVAL_RE.match(ok), ok
for not_ok in ("ок, но тут спорно", "не ок", "аудит ок — только поправь тест"):
    assert not APPROVAL_RE.match(not_ok), not_ok


def count_waves(events):
    """Копия логики из analyze_mr: волна = коммиты после очередной порции замечаний."""
    waves, pending = 0, False
    for _, kind in sorted(events, key=lambda e: e[0]):
        if kind == "comment":
            pending = True
        elif pending:
            waves += 1
            pending = False
    return waves


assert count_waves([(1, "comment"), (2, "commit")]) == 1
assert count_waves([(1, "comment"), (2, "comment"), (3, "commit"), (4, "commit")]) == 1, "порция замечаний = одна волна"
assert count_waves([(1, "comment"), (2, "commit"), (3, "comment"), (4, "commit")]) == 2
assert count_waves([(1, "commit"), (2, "commit")]) == 0, "коммиты до замечаний волнами не считаются"
assert count_waves([(1, "comment"), (2, "comment")]) == 0, "замечания без правки — не волна"

# отрезки lead time: неполная цепочка даёт None, а не ноль
assert segment(fri, mon) == {"calendar_h": 65.0, "work_h": 2.0}
assert segment(fri, None) is None and segment(None, mon) is None
assert segment(mon, fri) is None, "обратный отрезок не отрезок"


def pick_milestones(approvals, marks, merged):
    """Копия выбора вех из analyze_mr: последняя метка до мержа, последний аппрув до неё."""
    deploy_at = next((t for t in reversed(sorted(marks)) if not merged or t <= merged), None)
    approved_at = next((t for t in reversed(sorted(approvals)) if not deploy_at or t <= deploy_at), None)
    return approved_at, deploy_at


# у МР с доработками несколько аппрувов — операционный тот, после которого МР поехал
assert pick_milestones([1, 5], [6], 9) == (5, 6)
assert pick_milestones([1, 8], [6], 9) == (1, 6), "аппрув после метки не берём"
assert pick_milestones([1], [6, 20], 9) == (1, 6), "метку позже мержа не берём"
assert pick_milestones([1], [], 9) == (1, None), "без метки отгрузки отрезки не считаются"

# мерж-коммит не ответ на замечание, обычный — ответ (проверено на MR 674!29886:
# два комментария после последней правки закрыты только мержем мастера)
assert [len(c.get("parent_ids") or []) < 2 for c in (
    {"parent_ids": ["a"]}, {"parent_ids": ["a", "b"]}, {},
)] == [True, False, True]

# rebase: committed_date разъезжается с authored_date — детектор сравнивает до минут
assert "2026-08-21T10:38"[:16] != "2026-08-19T09:12"[:16]
assert "2026-08-21T10:38:20.993Z"[:16] == "2026-08-21T10:38:01.000Z"[:16], "секунды не считаем ребейзом"

print("ok")
