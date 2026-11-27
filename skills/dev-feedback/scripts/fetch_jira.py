#!/usr/bin/env python3
"""Jira-сторона фидбэка: свои задачи, точность оценок, задачи для коллег,
зависшие тикеты и роль в аудите чужого кода. Вход — почта разработчика в Jira."""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from jira import request  # noqa: E402
from fetch_mrs import TASK_KEY_RE  # noqa: E402

FIELDS = ("summary,issuetype,status,created,resolutiondate,updated,project,assignee,reporter,"
          "timeoriginalestimate,timespent")
CROSS_OK, FINAL_OK, REJECT = "Кросс-аудит ок", "Аудит ок", "Аудит не ок"
STALE_DAYS = 30
# «Отгружено» в этом Jira не в категории done и без resolution: без списка все отгрузки считались бы зависшими
SHIPPED = {"Отгружено"}
OVERRUN = 1.2


def search(jql):
    issues, token = [], None
    while True:
        params = {"jql": jql, "maxResults": 100, "fields": FIELDS}
        if token:
            params["nextPageToken"] = token
        page = request("GET", "/rest/api/3/search/jql?" + urlencode(params))
        issues += page.get("issues", [])
        token = page.get("nextPageToken")
        if not token or page.get("isLast"):
            return issues


def account_id(email):
    users = request("GET", "/rest/api/3/user/search?" + urlencode({"query": email})) or []
    if not users:
        sys.exit(f"Jira user not found: {email}")
    return users[0]["accountId"]


def who(person):
    return (person or {}).get("accountId")


def estimate_accuracy(issues, me, since):
    """Время берём как проставлено: оценка против списанного по закрытым в периоде задачам."""
    rows = []
    for i in issues:
        f = i["fields"]
        est, spent = f.get("timeoriginalestimate") or 0, f.get("timespent") or 0
        if who(f.get("assignee")) == me and est and spent and (f.get("resolutiondate") or "")[:10] >= since:
            rows.append((i["key"], est / 3600, spent / 3600))
    est_h = sum(e for _, e, _ in rows)
    spent_h = sum(s for _, _, s in rows)
    return {
        "n": len(rows),
        "estimate_h": round(est_h, 1),
        "spent_h": round(spent_h, 1),
        "spent_to_estimate": round(spent_h / est_h, 2) if est_h else None,
        "exactly_equal": sum(1 for _, e, s in rows if e == s),
        "overruns": [f"{k} {e:g}→{s:g} ч" for k, e, s in rows if s > e * OVERRUN],
    }


def status_steps(ticket):
    steps = []
    for h in ticket.get("changelog", {}).get("histories", []):
        author = h.get("author") or {}
        for it in h["items"]:
            if it["field"] == "status":
                steps.append({"at": h["created"], "by": author.get("accountId"),
                              "name": author.get("displayName", ""),
                              "from": it.get("fromString"), "to": it.get("toString")})
    return sorted(steps, key=lambda s: s["at"])


def audit_role(keys, me, since):
    """Переходы статусов аудита, сделанные разработчиком, и что было после его кросс-аудита."""
    def load(key):
        return request("GET", f"/rest/api/3/issue/{key}?expand=changelog&fields=reporter")

    with ThreadPoolExecutor(8) as pool:
        tickets = list(pool.map(load, keys))

    mine, final_ok_dates, after_cross, rejected_after_cross = Counter(), [], Counter(), []
    reporters = Counter()
    for t in tickets:
        steps = status_steps(t)
        touched = False
        for n, s in enumerate(steps):
            if s["by"] != me or s["at"][:10] < since:
                continue
            touched = True
            mine[f"{s['from']} → {s['to']}"] += 1
            if s["to"] == FINAL_OK:
                final_ok_dates.append(s["at"][:10])
            if s["to"] == CROSS_OK and n + 1 < len(steps) and steps[n + 1]["by"] != me:
                nxt = steps[n + 1]
                after_cross[f"{nxt['name']}: {nxt['to']}"] += 1
                if nxt["to"] == REJECT:
                    rejected_after_cross.append(t["key"])
        if touched:
            reporters[t["fields"]["reporter"]["displayName"]] += 1

    final_ok_dates.sort()
    return {
        "tickets_touched": sum(reporters.values()),
        "whose_tickets": dict(reporters.most_common()),
        "transitions_by_dev": dict(mine.most_common()),
        "final_ok": {"n": len(final_ok_dates), "first": final_ok_dates[0] if final_ok_dates else None,
                     "last": final_ok_dates[-1] if final_ok_dates else None},
        "next_after_cross_ok": dict(after_cross.most_common()),
        "rejected_after_cross_ok": rejected_after_cross,
    }


def main():
    parser = argparse.ArgumentParser(description="Jira side of a developer's feedback")
    parser.add_argument("--email", required=True, help="почта разработчика в Jira")
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--since", help="YYYY-MM-DD")
    period.add_argument("--months", type=int, default=3)
    parser.add_argument("--audit-project", default="FIRSTAUDIT", help="проект тикетов на проверку кода")
    parser.add_argument("--mrs-json", help="вывод fetch_mrs.py: отметить задачи без МР")
    args = parser.parse_args()

    since = args.since or (date.today() - timedelta(days=round(args.months * 30.4))).isoformat()
    with ThreadPoolExecutor(3) as pool:
        me = pool.submit(account_id, args.email)
        issues = pool.submit(search, f'assignee was "{args.email}" AND updated >= "{since}" ORDER BY created ASC')
        reported = pool.submit(search, f'reporter = "{args.email}" AND updated >= "{since}"')
        me, issues, reported = me.result(), issues.result(), reported.result()
    seen = {i["key"] for i in issues}

    # свои тикеты на аудит он тоже двигает после правок — роль ревьюера только на чужих
    audit_keys = [i["key"] for i in issues if i["fields"]["project"]["key"] == args.audit_project
                  and who(i["fields"].get("reporter")) != me]
    own = [i for i in issues if i["fields"]["project"]["key"] != args.audit_project]
    stale_before = (datetime.now() - timedelta(days=STALE_DAYS)).date().isoformat()

    mr_keys = set()
    if args.mrs_json:
        mrs = json.load(open(args.mrs_json, encoding="utf-8"))["merge_requests"]
        mr_keys = {k for m in mrs for k in TASK_KEY_RE.findall(f"{m.get('source_branch', '')} {m.get('title', '')}")}

    # тикет уходит к аудитору и тестировщику, поэтому «для коллег» — тот, где у разработчика нет своего МР;
    # без --mrs-json — тот, что ему не назначали никогда
    # заявки в сервисные проекты (доступы, выгрузки, тест-тикеты) — не задачи коллегам: берём только его рабочие проекты
    worked = mr_keys if args.mrs_json else seen
    own_projects = {i["fields"]["project"]["key"] for i in own}
    for_others = [i for i in reported
                  if who(i["fields"].get("reporter")) == me and who(i["fields"].get("assignee")) not in (me, None)
                  and i["fields"]["project"]["key"] in own_projects and i["key"] not in worked]

    def brief(i):
        f = i["fields"]
        return (f"{i['key']} [{f['status']['name']}] {(f.get('assignee') or {}).get('displayName', '-')}: "
                f"{f['summary'][:80]}")

    print(json.dumps({
        "email": args.email,
        "since": since,
        "own": {
            "n": len(own),
            "by_project": dict(Counter(i["fields"]["project"]["key"] for i in own).most_common()),
            "by_type": dict(Counter(i["fields"]["issuetype"]["name"] for i in own).most_common()),
            "by_status": dict(Counter(i["fields"]["status"]["name"] for i in own).most_common()),
            # работа, которой нет в GitLab или которая ушла под чужим ключом
            "without_mr": [brief(i) for i in own if i["key"] not in mr_keys
                           and who(i["fields"].get("assignee")) == me] if args.mrs_json else [],
        },
        "estimates": estimate_accuracy(own, me, since),
        # приняты, но давно не двигались: вопрос к триажу, не к скорости
        "stale_assigned": [brief(i) for i in own
                           if who(i["fields"].get("assignee")) == me
                           and i["fields"]["status"].get("statusCategory", {}).get("key") != "done"
                           and i["fields"]["status"]["name"] not in SHIPPED
                           and i["fields"]["updated"][:10] < stale_before],
        # разработчик сам ставит задачи коллегам: ТЗ и декомпозиция
        "written_for_others": [brief(i) for i in for_others],
        "audit": audit_role(audit_keys, me, since) if audit_keys else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
