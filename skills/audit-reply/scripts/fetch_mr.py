#!/usr/bin/env python3
"""Fetch open review threads for a GitLab merge request via glab.

Outputs JSON with MR metadata and the list of OPEN (unresolved) discussion
threads — each anchored to a file/line where possible — so the caller can map
every auditor comment to the code it refers to.

Usage:
    fetch_mr.py --url https://git.example.com/group/project/-/merge_requests/41
    fetch_mr.py --project group/project --iid 41 --hostname git.example.com
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import SELF_REVIEW, api, discussions as fetch_discussions, is_bot, mr_endpoint, parse_url  # noqa: E402

def thread_is_open(notes):
    """A thread needs attention if it has a resolvable note that is not resolved."""
    resolvable = [n for n in notes if n.get("resolvable")]
    if not resolvable:
        # General (non-resolvable) discussion: treat as open if it has real content.
        return True
    return any(not n.get("resolved") for n in resolvable)


def main():
    parser = argparse.ArgumentParser(description="Fetch open review threads for a GitLab MR")
    parser.add_argument("--url", help="Full MR url")
    parser.add_argument("--project", help="namespace/project (if no --url)")
    parser.add_argument("--iid", help="MR iid (if no --url)")
    parser.add_argument("--hostname", help="GitLab host (if no --url)")
    parser.add_argument("--all", action="store_true", help="Include resolved threads too")
    parser.add_argument("--author", help="Keep only threads opened by this reviewer (substring, e.g. isuhanov)")
    parser.add_argument("--since", help="Keep only threads opened on/after this date (YYYY-MM-DD)")
    parser.add_argument("--today", action="store_true", help="Shortcut for --since <today>")
    args = parser.parse_args()

    since = args.since
    if args.today:
        since = date.today().isoformat()

    if args.url:
        host, project_path, iid = parse_url(args.url)
    elif args.project and args.iid and args.hostname:
        host, project_path, iid = args.hostname, args.project, args.iid
    else:
        sys.stderr.write("Provide --url OR (--project AND --iid AND --hostname)\n")
        sys.exit(2)

    meta = api(host, "GET", mr_endpoint(project_path, iid), soft=True)
    if "error" in meta:
        sys.exit(f"{meta['error']}\nFailed to read MR. Check the url and that glab is authed:\n"
                 f"  glab auth status --hostname {host}")

    author = (meta.get("author") or {}).get("username", "")

    discussions = fetch_discussions(host, project_path, iid)

    threads = []
    skipped_resolved = 0
    for disc in discussions:
        notes = [n for n in (disc.get("notes") or []) if not n.get("system")]
        notes = [n for n in notes if not is_bot(n.get("author"), by_name=False)]
        if not notes:
            continue
        is_open = thread_is_open(disc.get("notes") or [])
        if not is_open and not args.all:
            skipped_resolved += 1
            continue

        root = notes[0]
        pos = root.get("position") or {}
        replies = notes[1:]
        threads.append({
            "discussion_id": disc.get("id"),
            "resolved": not is_open,
            "author": (root.get("author") or {}).get("username", ""),
            "is_author_self": (root.get("author") or {}).get("username", "") == author,
            # mr-ready posts its findings from the MR author's account: an unanswered one is still open
            "last_by_author": (notes[-1].get("author") or {}).get("username", "") == author
            and (bool(replies) or not (root.get("body") or "").startswith(SELF_REVIEW)),
            "created_at": (root.get("created_at") or "")[:10],
            "body": root.get("body") or "",
            "file": pos.get("new_path") or pos.get("old_path") or None,
            "new_line": pos.get("new_line"),
            "old_line": pos.get("old_line"),
            "is_general": not bool(pos),
            "reply_count": len(replies),
            "replies": [
                {
                    "author": (r.get("author") or {}).get("username", ""),
                    "body": r.get("body") or "",
                    "created_at": (r.get("created_at") or "")[:10],
                }
                for r in replies
            ],
        })

    open_total = len(threads)
    filtered_by_author = 0
    filtered_by_date = 0
    if args.author:
        needle = args.author.lower()
        kept = [t for t in threads if needle in (t["author"] or "").lower()]
        filtered_by_author = len(threads) - len(kept)
        threads = kept
    if since:
        kept = [t for t in threads if (t["created_at"] or "") >= since]
        filtered_by_date = len(threads) - len(kept)
        threads = kept

    output = {
        "host": host,
        "project_path": project_path,
        "iid": int(iid),
        "title": meta.get("title", ""),
        "author": author,
        "source_branch": meta.get("source_branch", ""),
        "target_branch": meta.get("target_branch", ""),
        "web_url": meta.get("web_url", ""),
        "filter_author": args.author or None,
        "filter_since": since or None,
        "open_total_before_filter": open_total,
        "filtered_out_by_author": filtered_by_author,
        "filtered_out_by_date": filtered_by_date,
        "open_thread_count": len(threads),
        "skipped_resolved": skipped_resolved,
        "threads": threads,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
