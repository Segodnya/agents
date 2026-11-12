#!/usr/bin/env python3
"""Самопроверка jira_checklist.py на пункте, который начинается со вложенного списка («1. * …»):
get рендерит его, put правит и вставляет в него пункты. Запуск: python3 jira_checklist_test.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import jira_checklist as jc  # noqa: E402


def p(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def li(*content):
    return {"type": "listItem", "content": list(content)}


def ol(*items):
    return {"type": "orderedList", "attrs": {"order": 1}, "content": list(items)}


def ul(*items):
    return {"type": "bulletList", "content": list(items)}


adf = {"type": "doc", "version": 1, "content": [ol(
    li(p("Кейсы"), ol(
        li(p("Позитивные"), ol(li(ul(li(p("первый")), li(p("второй")))))),
        li(p("Негативные"), ul(li(ul(li(p("третий")))))),
    )),
    li(p("Тип: front")),
)]}

fails = []

md = jc.to_md(adf)
items, _ = jc.parse_md(md)
want = [(0, "Кейсы"), (1, "Позитивные"), (2, "первый"), (2, "второй"), (1, "Негативные"), (2, "третий"),
        (0, "Тип: front")]
if items != want:
    fails.append(f"get: {items} != {want}\n{md}")

new_md = md.replace("второй", "второй, правка").replace("- третий", "- третий\n      - четвёртый")
try:
    plan, patched = jc.plan_patch(adf, md, new_md)
    got, _ = jc.parse_md(jc.to_md(patched))
    want_new, _ = jc.parse_md(new_md)
    if got != want_new:
        fails.append(f"put: {got} != {want_new}")
except SystemExit:
    fails.append(f"put: exit 1 on\n{new_md}")

print("\n".join(fails) or "ok")
sys.exit(1 if fails else 0)
