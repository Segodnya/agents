#!/usr/bin/env python3

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import get, is_bot, paginate  # noqa: E402

MAX_DIFF_LINES_PER_FILE = 500
CACHE_DIR = "/tmp/dev-feedback"


def truncate_diff(diff_text):
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

    changes_data = get(args.hostname, f"projects/{pid}/merge_requests/{iid}/changes") or {}

    changed_files = [{
        "path": change.get("new_path") or change.get("old_path", ""),
        "new_file": change.get("new_file", False),
        "deleted_file": change.get("deleted_file", False),
        "renamed_file": change.get("renamed_file", False),
        "diff": truncate_diff(change.get("diff") or ""),
    } for change in changes_data.get("changes", [])]

    all_notes = paginate(args.hostname, f"projects/{pid}/merge_requests/{iid}/notes", soft=True)

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
        "title": changes_data.get("title", ""),
        "description": changes_data.get("description", ""),
        "changed_files": changed_files,
        "comments": comments,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    # копия для гейта цитат в Step 4: диффы есть только в контексте субагента
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f"{pid}-{iid}.json"), "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
