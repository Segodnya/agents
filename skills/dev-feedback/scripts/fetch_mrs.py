#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

TASK_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")
ISO_FRACTION_RE = re.compile(r"\.\d+")
BOT_PATTERNS = ["bot", "deployer", "ci-", "gitlab-"]


def _count(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def utc_day(value):
    """ISO-8601 → дата в UTC. Ноты приходят с `Z`, коммиты со смещением — обрезать
    строку по 10 символам нельзя, у полуночных коммитов день уедет."""
    if not value:
        return None
    text = ISO_FRACTION_RE.sub("", value.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        sys.stderr.write(f"unparsed timestamp: {value}\n")
        return value[:10]


def task_key(branch):
    """Ключ тикета из имени ветки: feature/TASK-1163 → TASK-1163."""
    found = TASK_KEY_RE.search((branch or "").upper())
    return found.group(0) if found else None


def glab_api(endpoint, fields, hostname):
    cmd = ["glab", "api", endpoint, "-X", "GET", "--hostname", hostname]
    for key, value in fields.items():
        cmd.extend(["--field", f"{key}={value}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(f"glab api error: {result.stderr}\n")
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"JSON parse error for {endpoint}: {result.stdout[:200]}\n")
        return []


def fetch_all_pages(endpoint, fields, hostname):
    all_items = []
    page = 1
    while True:
        fields_with_page = {**fields, "per_page": "100", "page": str(page)}
        items = glab_api(endpoint, fields_with_page, hostname)
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
        page += 1
    return all_items


def activity_in_window(mr, frm, to, username, hostname):
    """Была ли по МР активность внутри окна. Сначала бесплатная проверка по полям
    списочного ответа, и только для неоднозначных — ноты и коммиты."""
    for field, reason in (("created_at", "создан"), ("merged_at", "смержен"), ("closed_at", "закрыт")):
        day = utc_day(mr.get(field))
        if day and frm <= day <= to:
            return reason

    # обе выдачи GitLab отдаёт новейшими вперёд, поэтому свежая активность всегда на
    # первой странице и пагинация не нужна — проверено на MR 674!29886
    pid, iid = mr["project_id"], mr["iid"]
    notes = glab_api(
        f"projects/{pid}/merge_requests/{iid}/notes",
        {"per_page": "100", "sort": "desc", "order_by": "created_at"},
        hostname,
    )
    for note in notes or []:
        if note.get("system"):
            continue
        author = (note.get("author", {}) or {}).get("username", "")
        if any(p in author.lower() for p in BOT_PATTERNS):
            continue
        day = utc_day(note.get("created_at"))
        if day and frm <= day <= to:
            return "обсуждение"

    commits = glab_api(f"projects/{pid}/merge_requests/{iid}/commits", {"per_page": "100"}, hostname)
    username_lower = username.lower()
    for commit in commits or []:
        haystack = f"{commit.get('author_name', '')} {commit.get('author_email', '')}".lower()
        day = utc_day(commit.get("committed_date"))
        if username_lower in haystack and day and frm <= day <= to:
            return "коммиты"

    return None


def check_has_commits_by_user(project_id, mr_iid, username, hostname):
    commits = glab_api(
        f"projects/{project_id}/merge_requests/{mr_iid}/commits",
        {},
        hostname,
    )
    username_lower = username.lower()
    for commit in commits:
        author_name = (commit.get("author_name") or "").lower()
        author_email = (commit.get("author_email") or "").lower()
        if username_lower in author_name or username_lower in author_email:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Fetch and categorize a developer's MRs active in the period")
    parser.add_argument("--username", required=True, help="GitLab username")
    parser.add_argument("--months", type=int, default=3, help="Period in months (default: 3)")
    parser.add_argument("--hostname", required=True, help="GitLab hostname")
    parser.add_argument(
        "--states",
        default="merged,closed,opened",
        help="какие состояния собирать через запятую (default: merged,closed,opened)",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    window_from = (now - timedelta(days=args.months * 30)).strftime("%Y-%m-%d")
    window_to = now.strftime("%Y-%m-%d")

    # предфильтр по updated_after, а не created_after: МР, заведённый до окна и
    # доделанный внутри него, — работа этого периода. Верхнюю границу API не задаём,
    # иначе МР, тронутый после окна, выпадет из отчёта за прошлый период;
    # обе границы проверяются локально по фактической активности
    base_fields = {
        "scope": "all",
        "updated_after": f"{window_from}T00:00:00Z",
    }

    # closed и opened берём наравне с merged: разработчик мог завести несколько МР на
    # одну задачу, а незаконченная и выброшенная работа — такой же факт периода
    states = [s.strip() for s in args.states.split(",") if s.strip()]

    def fetch_by(role):
        found = []
        for state in states:
            sys.stderr.write(f"Fetching {state} MRs {role} {args.username}...\n")
            found.extend(fetch_all_pages(
                "merge_requests",
                {**base_fields, "state": state, f"{role}_username": args.username},
                args.hostname,
            ))
        return found

    authored_mrs = fetch_by("author")
    assigned_mrs = fetch_by("assignee")

    authored_keys = {(mr["project_id"], mr["iid"]) for mr in authored_mrs}
    assigned_keys = {(mr["project_id"], mr["iid"]) for mr in assigned_mrs}

    all_mrs_by_key = {}
    for mr in authored_mrs + assigned_mrs:
        key = (mr["project_id"], mr["iid"])
        if key not in all_mrs_by_key:
            all_mrs_by_key[key] = mr

    categorized = []
    skipped_author_only = 0
    skipped_no_activity = 0

    sys.stderr.write(f"Checking activity in {window_from}..{window_to} for {len(all_mrs_by_key)} MRs...\n")
    for key, mr in sorted(all_mrs_by_key.items(), key=lambda x: x[1].get("created_at", "")):
        is_author = key in authored_keys
        is_assignee = key in assigned_keys

        # МР без активности в окне — не работа этого периода, в каком бы он ни был состоянии
        activity = activity_in_window(mr, window_from, window_to, args.username, args.hostname)
        if not activity:
            skipped_no_activity += 1
            sys.stderr.write(f"  Skipped MR !{mr['iid']} ({mr.get('state')}, no activity in window)\n")
            continue

        if is_author and is_assignee:
            category = "authored"
        elif is_author and not is_assignee:
            has_commits = check_has_commits_by_user(
                mr["project_id"], mr["iid"], args.username, args.hostname
            )
            if not has_commits:
                skipped_author_only += 1
                sys.stderr.write(
                    f"  Skipped MR !{mr['iid']} (author_only, no commits by {args.username})\n"
                )
                continue
            category = "author_only"
        else:
            category = "assignee_only"

        project_path = mr.get("references", {}).get("full", "").split("!")[0].rstrip("/")
        if not project_path:
            project_path = mr.get("web_url", "").split("/-/")[0].split("/", 3)[-1] if "/-/" in mr.get("web_url", "") else str(mr["project_id"])

        categorized.append({
            "project_id": mr["project_id"],
            "project_name": project_path,
            "iid": mr["iid"],
            "title": mr.get("title", ""),
            "web_url": mr.get("web_url", ""),
            "category": category,
            "created_at": mr.get("created_at", "")[:10],
            "merged_at": mr.get("merged_at", "")[:10] if mr.get("merged_at") else "",
            "created_at_iso": mr.get("created_at", ""),
            "merged_at_iso": mr.get("merged_at", "") or "",
            "closed_at_iso": mr.get("closed_at", "") or "",
            "state": mr.get("state", ""),
            "draft": mr.get("draft", False),
            "updated_at_iso": mr.get("updated_at", ""),
            "activity_in_window": activity,
            "source_branch": mr.get("source_branch", ""),
            "target_branch": mr.get("target_branch", ""),
            "labels": mr.get("labels", []),
        })

    # одна задача — несколько МР: ветка повторяется, либо ключ задачи в имени ветки
    by_branch = {}
    for mr in categorized:
        key = task_key(mr["source_branch"]) or task_key(mr["title"]) or mr["source_branch"]
        by_branch.setdefault(key, set()).add((mr["project_name"], mr["iid"], mr["state"]))
    multi = {
        key: sorted(f"{p}!{i} ({s})" for p, i, s in mrs)
        for key, mrs in by_branch.items() if len(mrs) > 1
    }

    projects = sorted(set(mr["project_name"] for mr in categorized))
    summary = {
        "total": len(categorized),
        "merged": sum(1 for mr in categorized if mr["state"] == "merged"),
        "closed": sum(1 for mr in categorized if mr["state"] == "closed"),
        "opened": sum(1 for mr in categorized if mr["state"] == "opened"),
        "skipped_no_activity": skipped_no_activity,
        "activity_reasons": _count(mr["activity_in_window"] for mr in categorized),
        "authored": sum(1 for mr in categorized if mr["category"] == "authored"),
        "author_only": sum(1 for mr in categorized if mr["category"] == "author_only"),
        "assignee_only": sum(1 for mr in categorized if mr["category"] == "assignee_only"),
        "skipped_no_commits": skipped_author_only,
        "projects": projects,
        "multi_mr_tasks": multi,
    }

    output = {
        "username": args.username,
        "period": {
            "from": window_from,
            "to": window_to,
            "critical": "МР попадает в отчёт только при активности внутри окна",
        },
        "summary": summary,
        "merge_requests": categorized,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))

    sys.stderr.write(
        f"\nDone: {summary['total']} MRs "
        f"(authored: {summary['authored']}, "
        f"author_only: {summary['author_only']}, "
        f"assignee_only: {summary['assignee_only']}, "
        f"merged: {summary['merged']}, closed: {summary['closed']}, opened: {summary['opened']}, "
        f"skipped: {skipped_author_only} no-commits / {skipped_no_activity} no-activity)\n"
    )


if __name__ == "__main__":
    main()
