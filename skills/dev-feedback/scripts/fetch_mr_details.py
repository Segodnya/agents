#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys

MAX_DIFF_LINES_PER_FILE = 500
BOT_PATTERNS = ["bot", "deployer", "ci-", "gitlab-"]


def glab_api(endpoint, fields, hostname):
    cmd = ["glab", "api", endpoint, "-X", "GET", "--hostname", hostname]
    for key, value in fields.items():
        cmd.extend(["--field", f"{key}={value}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(f"glab api error: {result.stderr}\n")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"JSON parse error: {result.stdout[:200]}\n")
        return None


def is_bot(username):
    username_lower = (username or "").lower()
    return any(pattern in username_lower for pattern in BOT_PATTERNS)


def truncate_diff(diff_text):
    if not diff_text:
        return ""
    lines = diff_text.split("\n")
    if len(lines) <= MAX_DIFF_LINES_PER_FILE:
        return diff_text
    return "\n".join(lines[:MAX_DIFF_LINES_PER_FILE]) + f"\n... truncated ({len(lines)} lines total)"


def main():
    parser = argparse.ArgumentParser(description="Fetch diff and comments for a single MR")
    parser.add_argument("--project-id", required=True, help="GitLab project ID")
    parser.add_argument("--mr-iid", required=True, help="MR IID within the project")
    parser.add_argument("--hostname", required=True, help="GitLab hostname")
    args = parser.parse_args()

    pid = args.project_id
    iid = args.mr_iid

    changes_data = glab_api(f"projects/{pid}/merge_requests/{iid}/changes", {}, args.hostname)

    changed_files = []
    if changes_data and "changes" in changes_data:
        for change in changes_data["changes"]:
            changed_files.append({
                "path": change.get("new_path") or change.get("old_path", ""),
                "new_file": change.get("new_file", False),
                "deleted_file": change.get("deleted_file", False),
                "renamed_file": change.get("renamed_file", False),
                "diff": truncate_diff(change.get("diff", "")),
            })

    all_notes = []
    page = 1
    while True:
        notes = glab_api(
            f"projects/{pid}/merge_requests/{iid}/notes",
            {"per_page": "100", "page": str(page)},
            args.hostname,
        )
        if not notes:
            break
        all_notes.extend(notes)
        if len(notes) < 100:
            break
        page += 1

    comments = []
    for note in all_notes:
        if note.get("system"):
            continue
        author = note.get("author", {}).get("username", "")
        if is_bot(author):
            continue
        comment = {
            "author": author,
            "body": note.get("body", ""),
            "created_at": note.get("created_at", "")[:10],
        }
        if note.get("type") == "DiffNote":
            comment["type"] = "diff_note"
            position = note.get("position", {})
            comment["path"] = position.get("new_path") or position.get("old_path", "")
        else:
            comment["type"] = "general"
        comments.append(comment)

    output = {
        "project_id": int(pid),
        "iid": int(iid),
        "title": changes_data.get("title", "") if changes_data else "",
        "description": changes_data.get("description", "") if changes_data else "",
        "changed_files": changed_files,
        "comments": comments,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
