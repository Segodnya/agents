#!/usr/bin/env python3
"""GitLab-активность разработчика за пределами своих МР: комментарии на чужих МР
за период и история активности по месяцам с перерывами (стаж и срок на грейде)."""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import get, paginate  # noqa: E402

GAP_DAYS = 14
BODY_CHARS = 400


def gaps(days, min_days):
    """Отсортированные даты 'YYYY-MM-DD' → перерывы длиннее min_days."""
    out = []
    for prev, cur in zip(days, days[1:]):
        length = (date.fromisoformat(cur) - date.fromisoformat(prev)).days
        if length > min_days:
            out.append({"from": prev, "to": cur, "days": length})
    return out


def main():
    parser = argparse.ArgumentParser(description="Developer's review comments and activity history")
    parser.add_argument("--username", required=True)
    parser.add_argument("--hostname", required=True)
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--since", help="YYYY-MM-DD")
    period.add_argument("--months", type=int, default=3)
    parser.add_argument("--history-years", type=int, default=3, help="глубина истории активности")
    args = parser.parse_args()

    since = args.since or (date.today() - timedelta(days=round(args.months * 30.4))).isoformat()
    history_from = (date.today() - timedelta(days=365 * args.history_years)).isoformat()
    users = get(args.hostname, "users", {"username": args.username}) or []
    if not users:
        sys.exit(f"GitLab user not found: {args.username}")
    events = paginate(args.hostname, f"users/{users[0]['id']}/events?after={history_from}",
                      soft=True, until_empty=True)

    days = sorted({e["created_at"][:10] for e in events})
    by_month = Counter(e["created_at"][:7] for e in events)

    notes = [e for e in events if e.get("action_name") == "commented on" and e["created_at"][:10] >= since
             and (e.get("note") or {}).get("noteable_type") == "MergeRequest"]
    mr_refs = sorted({(e["project_id"], e["note"]["noteable_iid"]) for e in notes})

    def load(ref):
        return get(args.hostname, f"projects/{ref[0]}/merge_requests/{ref[1]}")

    with ThreadPoolExecutor(8) as pool:
        mrs = dict(zip(mr_refs, pool.map(load, mr_refs)))

    review = []
    for e in sorted(notes, key=lambda x: x["created_at"]):
        ref = (e["project_id"], e["note"]["noteable_iid"])
        mr = mrs[ref]
        if not mr or mr["author"]["username"].lower() == args.username.lower():
            continue
        review.append({
            "mr": (mr.get("references") or {}).get("full", f"{ref[0]}!{ref[1]}"),
            "mr_author": mr["author"]["username"],
            "at": e["created_at"][:10],
            "path": (e["note"].get("position") or {}).get("new_path"),
            "body": e["note"]["body"][:BODY_CHARS],
        })

    print(json.dumps({
        "username": args.username,
        "since": since,
        "activity": {
            "first_event": days[0] if days else None,
            "last_event": days[-1] if days else None,
            "history_from": history_from,
            "events_by_month": dict(sorted(by_month.items())),
            "gaps_over_days": GAP_DAYS,
            "gaps": gaps(days, GAP_DAYS),
        },
        "review": {
            "comments": len(review),
            "mrs": len({r["mr"] for r in review}),
            "by_mr_author": dict(Counter(r["mr_author"] for r in review).most_common()),
            "notes": review,
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
