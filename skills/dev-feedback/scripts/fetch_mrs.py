#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone


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
    parser = argparse.ArgumentParser(description="Fetch and categorize merged MRs for a developer")
    parser.add_argument("--username", required=True, help="GitLab username")
    parser.add_argument("--months", type=int, default=3, help="Period in months (default: 3)")
    parser.add_argument("--hostname", required=True, help="GitLab hostname")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    created_after = (now - timedelta(days=args.months * 30)).strftime("%Y-%m-%dT00:00:00Z")
    created_before = now.strftime("%Y-%m-%dT23:59:59Z")

    base_fields = {
        "scope": "all",
        "state": "merged",
        "created_after": created_after,
        "created_before": created_before,
    }

    sys.stderr.write(f"Fetching MRs authored by {args.username}...\n")
    authored_mrs = fetch_all_pages(
        "merge_requests",
        {**base_fields, "author_username": args.username},
        args.hostname,
    )

    sys.stderr.write(f"Fetching MRs assigned to {args.username}...\n")
    assigned_mrs = fetch_all_pages(
        "merge_requests",
        {**base_fields, "assignee_username": args.username},
        args.hostname,
    )

    authored_keys = {(mr["project_id"], mr["iid"]) for mr in authored_mrs}
    assigned_keys = {(mr["project_id"], mr["iid"]) for mr in assigned_mrs}

    all_mrs_by_key = {}
    for mr in authored_mrs + assigned_mrs:
        key = (mr["project_id"], mr["iid"])
        if key not in all_mrs_by_key:
            all_mrs_by_key[key] = mr

    categorized = []
    skipped_author_only = 0

    for key, mr in sorted(all_mrs_by_key.items(), key=lambda x: x[1].get("created_at", "")):
        is_author = key in authored_keys
        is_assignee = key in assigned_keys

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
            "labels": mr.get("labels", []),
        })

    projects = sorted(set(mr["project_name"] for mr in categorized))
    summary = {
        "total": len(categorized),
        "authored": sum(1 for mr in categorized if mr["category"] == "authored"),
        "author_only": sum(1 for mr in categorized if mr["category"] == "author_only"),
        "assignee_only": sum(1 for mr in categorized if mr["category"] == "assignee_only"),
        "skipped_no_commits": skipped_author_only,
        "projects": projects,
    }

    output = {
        "username": args.username,
        "period": {
            "from": created_after[:10],
            "to": created_before[:10],
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
        f"skipped: {skipped_author_only})\n"
    )


if __name__ == "__main__":
    main()
