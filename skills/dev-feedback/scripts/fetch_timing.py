#!/usr/bin/env python3
"""Считает скорость реакции разработчика по МР-ам: lead time, время на замечание,
волны правок, количество комментариев. Вход — JSON от fetch_mrs.py на stdin."""

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BOT_PATTERNS = ["bot", "deployer", "ci-", "gitlab-"]
# «ок», «аудит ок», «кросс-аудит ок», «кросс ок» — это апрув, а не замечание
APPROVAL_RE = re.compile(r"^\s*(кросс[-\s]?)?(аудит\s+)?ок\s*[.!]?\s*$", re.IGNORECASE)
FRACTION_RE = re.compile(r"\.\d+")
WORK_START, WORK_END = 10, 19


def glab_api(endpoint, fields, hostname):
    cmd = ["glab", "api", endpoint, "-X", "GET", "--hostname", hostname]
    for key, value in fields.items():
        cmd.extend(["--field", f"{key}={value}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(f"glab api error {endpoint}: {result.stderr}\n")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"JSON parse error {endpoint}: {result.stdout[:200]}\n")
        return None


def paginate(endpoint, hostname):
    items, page = [], 1
    while True:
        chunk = glab_api(endpoint, {"per_page": "100", "page": str(page)}, hostname)
        if not chunk:
            break
        items.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return items


def parse_ts(value, tz):
    """GitLab отдаёт ноты в UTC с миллисекундами, коммиты — со смещением.
    Лексикографическое сравнение на этой смеси врёт, поэтому только разбор."""
    if not value:
        return None
    text = FRACTION_RE.sub("", value.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(text).astimezone(tz)
    except ValueError:
        sys.stderr.write(f"unparsed timestamp: {value}\n")
        return None


def work_hours(start, end):
    """Пересечение интервала с пн–пт 10:00–19:00 локального времени."""
    if not start or not end or end <= start:
        return 0.0
    total, cur = 0.0, start
    while cur < end:
        midnight = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        seg_end = min(end, midnight + timedelta(days=1))
        if cur.weekday() < 5:
            s = max(cur, midnight.replace(hour=WORK_START))
            e = min(seg_end, midnight.replace(hour=WORK_END))
            if e > s:
                total += (e - s).total_seconds()
        cur = seg_end
    return round(total / 3600, 1)


def calendar_hours(start, end):
    if not start or not end or end <= start:
        return 0.0
    return round((end - start).total_seconds() / 3600, 1)


def segment(start, end):
    if not start or not end or end <= start:
        return None
    return {"calendar_h": calendar_hours(start, end), "work_h": work_hours(start, end)}


def is_bot(username):
    return any(p in (username or "").lower() for p in BOT_PATTERNS)


def identity_tokens(username, hostname):
    """Коммиты подписаны именем и почтой, а не username — собираем все варианты."""
    tokens = {username.lower()}
    users = glab_api("users", {"username": username}, hostname) or []
    for user in users:
        for key in ("name", "email", "public_email"):
            if user.get(key):
                tokens.add(user[key].lower())
    return {t for t in tokens if t}


def commit_is_mine(commit, tokens):
    haystack = f"{commit.get('author_name', '')} {commit.get('author_email', '')}".lower()
    return any(token in haystack for token in tokens)


def _hist(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items()))


def median(values):
    return round(statistics.median(values), 1) if values else None


def mean(values):
    return round(statistics.mean(values), 1) if values else None


def analyze_mr(mr, username, tokens, hostname, tz, now):
    pid, iid = mr["project_id"], mr["iid"]
    created = parse_ts(mr.get("created_at_iso") or mr.get("created_at"), tz)
    merged = parse_ts(mr.get("merged_at_iso") or mr.get("merged_at"), tz)
    closed = parse_ts(mr.get("closed_at_iso"), tz)
    state = mr.get("state") or ("merged" if merged else "")

    notes = paginate(f"projects/{pid}/merge_requests/{iid}/notes", hostname)
    commits = paginate(f"projects/{pid}/merge_requests/{iid}/commits", hostname)

    comments, approvals_at, deploy_marks, reviewers = [], [], [], set()
    for note in notes:
        if note.get("system"):
            continue
        author = note.get("author", {}).get("username", "")
        body = (note.get("body") or "").strip()
        ts = parse_ts(note.get("created_at"), tz)
        if not ts:
            continue
        if body == ".":  # веха «тикет взят в отгрузку», автор deployer — не шум
            deploy_marks.append(ts)
            continue
        if is_bot(author) or author.lower() == username.lower():
            continue
        if APPROVAL_RE.match(body):
            approvals_at.append(ts)
            continue
        reviewers.add(author)
        comments.append({"at": ts, "author": author, "body": body[:160]})
    comments.sort(key=lambda c: c["at"])
    approvals_at.sort()
    deploy_marks.sort()

    # почту чужого юзера GitLab не отдаёт, опознание идёт по подстроке — показываем,
    # какие подписи в коммитах встретились и какие из них не сопоставились
    identities = {}
    for c in commits:
        key = (c.get("author_name", ""), c.get("author_email", ""))
        hit, total = identities.get(key, (commit_is_mine(c, tokens), 0))
        identities[key] = (hit, total + 1)

    mine = [c for c in commits if commit_is_mine(c, tokens)]
    all_commits = sorted(t for t in (parse_ts(c.get("committed_date"), tz) for c in mine) if t)
    # мерж мастера в ветку — не ответ на замечание; для реакции и волн берём только правки
    my_commits = sorted(
        t for t in (parse_ts(c.get("committed_date"), tz) for c in mine if len(c.get("parent_ids") or []) < 2)
        if t
    )
    # rebase переписывает committed_date: расхождение с authored_date помечаем, а не молчим
    rebased = sum(1 for c in mine if c.get("committed_date", "")[:16] != c.get("authored_date", "")[:16])

    # волна = серия коммитов, пришедшая после очередной порции замечаний
    events = [(c["at"], "comment") for c in comments] + [(t, "commit") for t in my_commits]
    events.sort(key=lambda e: e[0])
    waves, pending = 0, False
    for _, kind in events:
        if kind == "comment":
            pending = True
        elif pending:
            waves += 1
            pending = False

    reactions = []
    for comment in comments:
        fix = next((t for t in my_commits if t > comment["at"]), None)
        reactions.append({
            "author": comment["author"],
            "at": comment["at"].isoformat(),
            "body": comment["body"],
            "fixed": fix is not None,
            "calendar_h": calendar_hours(comment["at"], fix) if fix else None,
            "work_h": work_hours(comment["at"], fix) if fix else None,
        })

    # операционные вехи: последняя метка отгрузки до мержа и последний аппрув до неё
    deploy_at = next((t for t in reversed(deploy_marks) if not merged or t <= merged), None)
    approved_at = next((t for t in reversed(approvals_at) if not deploy_at or t <= deploy_at), None)
    segments = {
        "created_to_approve": segment(created, approved_at),
        "approve_to_deploy": segment(approved_at, deploy_at),
        "deploy_to_merge": segment(deploy_at, merged),
    }

    outcome = None
    if deploy_at:
        # тут считаем ВСЕ коммиты: мерж мастера после метки — и есть признак конфликта
        after_commits = sum(1 for t in all_commits if t > deploy_at)
        after_comments = sum(1 for c in comments if c["at"] > deploy_at)
        outcome = "доработки" if after_commits and after_comments else "конфликт" if after_commits else "чисто"

    warnings = []
    if deploy_at and approved_at is None and approvals_at:
        warnings.append(f"{mr['project_name']}!{iid}: аппрувы есть, но все после метки отгрузки")
    if rebased:
        warnings.append(
            f"{mr['project_name']}!{iid}: {rebased} коммитов с переписанной датой (rebase) — "
            "время реакции и волны по этому МР недостоверны"
        )
    if commits and not mine:
        warnings.append(f"{mr['project_name']}!{iid}: ни один коммит не сопоставлен с {username}")
    if not merged and state != "closed":
        warnings.append(f"{mr['project_name']}!{iid}: нет merged_at")
    if state == "closed" and deploy_at:
        warnings.append(
            f"{mr['project_name']}!{iid}: закрыт, хотя был взят в отгрузку — "
            "правка почти наверняка уехала другим МР, не считай задачу брошенной"
        )

    return {
        "iid": iid,
        "project_name": mr.get("project_name", str(pid)),
        "web_url": mr.get("web_url", ""),
        "category": mr.get("category", ""),
        "state": state,
        "draft": bool(mr.get("draft")),
        "target_branch": mr.get("target_branch", ""),
        "closed_at": closed.isoformat() if closed else None,
        "title": mr.get("title", ""),
        "lead_calendar_h": calendar_hours(created, merged),
        "lead_work_h": work_hours(created, merged),
        "life_calendar_h": calendar_hours(created, closed) if state == "closed" else None,
        "life_work_h": work_hours(created, closed) if state == "closed" else None,
        # открытый МР ещё живёт — считаем возраст на момент прогона
        "age_calendar_h": calendar_hours(created, now) if state == "opened" else None,
        "age_work_h": work_hours(created, now) if state == "opened" else None,
        # закрыт после аппрува = работа доведена до конца и выброшена
        "approved_before_close": bool(state == "closed" and approvals_at),
        "segments": segments,
        "approved_at": approved_at.isoformat() if approved_at else None,
        "deploy_at": deploy_at.isoformat() if deploy_at else None,
        "deploy_mark_hour": deploy_at.hour if deploy_at else None,
        "deploy_outcome": outcome,
        "comments_count": len(comments),
        "approvals_count": len(approvals_at),
        "reviewers": sorted(reviewers),
        "commits_mine": len(all_commits),
        "commits_fix": len(my_commits),
        "commit_identities": identities,
        "waves": waves,
        "reactions": reactions,
        "warnings": warnings,
    }


def aggregate(rows):
    own = [r for r in rows if r["category"] != "assignee_only"]
    if not own:
        own = rows
    # ревью получали и закрытые МР, а вот lead time и вехи отгрузки есть только у merged
    merged_rows = [r for r in own if r["state"] == "merged"]
    closed_rows = [r for r in own if r["state"] == "closed"]
    open_rows = [r for r in own if r["state"] == "opened"]
    reactions = [x for r in own for x in r["reactions"]]
    fixed = [x for x in reactions if x["fixed"]]
    comment_counts = [r["comments_count"] for r in own]
    waves = [r["waves"] for r in own]
    lead_cal = [r["lead_calendar_h"] for r in merged_rows if r["lead_calendar_h"]]
    lead_work = [r["lead_work_h"] for r in merged_rows if r["lead_work_h"]]

    wave_dist = {}
    for w in waves:
        wave_dist[str(w)] = wave_dist.get(str(w), 0) + 1

    # доли отрезков считаются по суммам и только на МР с полной цепочкой:
    # медианы отрезков в медиану целого не складываются
    seg_keys = ("created_to_approve", "approve_to_deploy", "deploy_to_merge")
    full = [r for r in merged_rows if all(r["segments"][k] for k in seg_keys)]
    full_sum = sum(r["segments"][k]["work_h"] for r in full for k in seg_keys)
    lead_split = {}
    for key in seg_keys:
        got = [r["segments"][key] for r in merged_rows if r["segments"][key]]
        lead_split[key] = {
            "n": len(got),
            "work_median": median([s["work_h"] for s in got]),
            "calendar_median": median([s["calendar_h"] for s in got]),
            "work_share_pct": (
                round(100 * sum(r["segments"][key]["work_h"] for r in full) / full_sum)
                if full_sum else None
            ),
        }

    outcomes, mark_hours = {}, {}
    for r in merged_rows:
        if r["deploy_outcome"]:
            outcomes[r["deploy_outcome"]] = outcomes.get(r["deploy_outcome"], 0) + 1
        if r["deploy_mark_hour"] is not None:
            mark_hours[str(r["deploy_mark_hour"])] = mark_hours.get(str(r["deploy_mark_hour"]), 0) + 1

    return {
        "mrs": len(own),
        "mrs_merged": len(merged_rows),
        "open": {
            "mrs": len(open_rows),
            "drafts": sum(1 for r in open_rows if r["draft"]),
            # заапрувлен, но ещё не смержен — работа сделана и ждёт поезда
            "approved_waiting": sum(1 for r in open_rows if r["approved_at"]),
            "with_unanswered_comments": sum(
                1 for r in open_rows if any(not x["fixed"] for x in r["reactions"])
            ),
            "age_work_median": median([r["age_work_h"] for r in open_rows if r["age_work_h"]]),
            "age_work_max": max([r["age_work_h"] for r in open_rows if r["age_work_h"]], default=None),
            "list": [
                f"{r['project_name']}!{r['iid']}{' [draft]' if r['draft'] else ''} — {r['title'][:55]}"
                for r in sorted(open_rows, key=lambda x: -(x["age_work_h"] or 0))
            ],
        },
        "closed": {
            "mrs": len(closed_rows),
            "approved_before_close": sum(1 for r in closed_rows if r["approved_before_close"]),
            "with_comments": sum(1 for r in closed_rows if r["comments_count"]),
            "commits": sum(r["commits_mine"] for r in closed_rows),
            "life_work_median": median([r["life_work_h"] for r in closed_rows if r["life_work_h"]]),
            # закрытие МР в релизную ветку рутинно, а пачка закрытий в один день —
            # чаще плановая чистка, чем брошенная работа: проверь оба, прежде чем судить
            "to_release_branch": sum(
                1 for r in closed_rows if r["target_branch"] not in ("master", "main", "develop")
            ),
            "close_date_hist": _hist(r["closed_at"][:10] for r in closed_rows if r["closed_at"]),
            "list": [
                f"{r['project_name']}!{r['iid']} → {r['target_branch']} — {r['title'][:55]}"
                for r in closed_rows
            ],
        },
        "lead_time_h": {
            "calendar_median": median(lead_cal),
            "work_median": median(lead_work),
            "work_share_pct": round(100 * sum(lead_work) / sum(lead_cal)) if sum(lead_cal) else None,
            "max_calendar": max(lead_cal) if lead_cal else None,
        },
        "lead_split_h": {
            "mrs_with_full_chain": len(full),
            "mrs_without_deploy_mark": sum(1 for r in merged_rows if not r["deploy_at"]),
            "mrs_without_approval": sum(1 for r in merged_rows if not r["approved_at"]),
            **lead_split,
        },
        "deploy_outcome": outcomes,
        "deploy_mark_hour_hist": dict(sorted(mark_hours.items(), key=lambda kv: int(kv[0]))),
        "comments": {
            "total": sum(comment_counts),
            "per_mr_avg": mean(comment_counts),
            "per_mr_median": median(comment_counts),
            "max_on_one_mr": max(comment_counts) if comment_counts else 0,
            "mrs_without_comments": sum(1 for c in comment_counts if c == 0),
        },
        "reaction_h": {
            "n_comments": len(reactions),
            "n_with_fix": len(fixed),
            "n_without_fix": len(reactions) - len(fixed),
            "calendar_median": median([x["calendar_h"] for x in fixed]),
            "work_median": median([x["work_h"] for x in fixed]),
            "work_p90": (
                round(statistics.quantiles([x["work_h"] for x in fixed], n=10)[-1], 1)
                if len(fixed) >= 10 else None
            ),
        },
        "waves": {
            "median": median(waves),
            "avg": mean(waves),
            "max": max(waves) if waves else 0,
            "distribution": dict(sorted(wave_dist.items(), key=lambda kv: int(kv[0]))),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Timing metrics for a developer's MRs")
    parser.add_argument("--username", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--mrs-json", required=True, help="файл с выводом fetch_mrs.py, либо '-' для stdin")
    parser.add_argument("--tz-offset", type=float, default=3.0, help="смещение рабочей зоны от UTC (default: 3 = MSK)")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.mrs_json == "-" else open(args.mrs_json, encoding="utf-8").read()
    payload = json.loads(raw)
    mrs = payload["merge_requests"]
    tz = timezone(timedelta(hours=args.tz_offset))
    now = datetime.now(tz)
    tokens = identity_tokens(args.username, args.hostname)

    rows, warnings, identities = [], [], {}
    for i, mr in enumerate(mrs, 1):
        sys.stderr.write(f"[{i}/{len(mrs)}] {mr['project_name']}!{mr['iid']}\n")
        row = analyze_mr(mr, args.username, tokens, args.hostname, tz, now)
        warnings.extend(row.pop("warnings"))
        for key, (hit, count) in row.pop("commit_identities").items():
            prev = identities.get(key, (hit, 0))
            identities[key] = (hit, prev[1] + count)
        rows.append(row)

    # в коммитах МР лежит весь влитый мастер, поэтому чужих подписей всегда десятки:
    # показываем опознанные целиком и три крупнейшие чужие — чтобы вторую почту
    # разработчика было видно, но список не превращался в шум
    ordered = sorted(identities.items(), key=lambda kv: -kv[1][1])
    signatures = [
        {"author_name": name, "author_email": email, "commits": count, "matched": hit}
        for (name, email), (hit, count) in ordered if hit
    ]
    others = [
        {"author_name": name, "author_email": email, "commits": count, "matched": False}
        for (name, email), (hit, count) in ordered if not hit
    ]
    signatures.extend(others[:3])

    print(json.dumps({
        "username": args.username,
        "period": payload.get("period", {}),
        "work_window": f"пн–пт {WORK_START}:00–{WORK_END}:00, UTC{args.tz_offset:+g}",
        "identity_tokens": sorted(tokens),
        "commit_signatures": signatures,
        "commit_signatures_other_count": len(others),
        "totals": aggregate(rows),
        "per_mr": rows,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
