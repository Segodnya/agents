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
import subprocess
import sys
from datetime import date
from urllib.parse import urlparse, quote

SYSTEM_BOT_PATTERNS = ["bot", "deployer", "ci-", "gitlab-"]


def glab_api(endpoint, hostname, fields=None):
    cmd = ["glab", "api", endpoint, "-X", "GET", "--hostname", hostname]
    for key, value in (fields or {}).items():
        cmd.extend(["--field", f"{key}={value}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(f"glab api error ({endpoint}): {result.stderr}\n")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"JSON parse error ({endpoint}): {result.stdout[:200]}\n")
        return None


def parse_url(url):
    """https://HOST/group/sub/project/-/merge_requests/41 -> (host, 'group/sub/project', '41')."""
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path
    marker = "/-/merge_requests/"
    if marker not in path:
        sys.stderr.write(f"Not an MR url (no '{marker}'): {url}\n")
        sys.exit(2)
    project_path, iid_part = path.split(marker, 1)
    project_path = project_path.strip("/")
    iid = iid_part.strip("/").split("/")[0].split("?")[0]
    return host, project_path, iid


def is_bot(username):
    low = (username or "").lower()
    return any(p in low for p in SYSTEM_BOT_PATTERNS)


def paginate(endpoint, hostname):
    items = []
    page = 1
    while True:
        chunk = glab_api(endpoint, hostname, {"per_page": "100", "page": str(page)})
        if not chunk:
            break
        items.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return items


def thread_is_open(notes):
    """A thread needs attention if it has a resolvable note that is not resolved."""
    resolvable = [n for n in notes if n.get("resolvable")]
    if not resolvable:
        # General (non-resolvable) discussion: treat as open if it has real content.
        return True
    return any(not n.get("resolved") for n in resolvable)


def shorten(text, limit=4000):
    if text and len(text) > limit:
        return text[:limit] + f"\n... [truncated, {len(text)} chars total]"
    return text or ""


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

    enc = quote(project_path, safe="")
    base = f"projects/{enc}/merge_requests/{iid}"

    meta = glab_api(base, host)
    if not meta:
        sys.stderr.write(
            "Failed to read MR. Check the url and that glab is authed:\n"
            f"  glab auth status --hostname {host}\n"
        )
        sys.exit(1)

    author = (meta.get("author") or {}).get("username", "")

    discussions = paginate(f"{base}/discussions", host)

    threads = []
    skipped_resolved = 0
    for disc in discussions:
        notes = [n for n in (disc.get("notes") or []) if not n.get("system")]
        notes = [n for n in notes if not is_bot((n.get("author") or {}).get("username"))]
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
            "created_at": (root.get("created_at") or "")[:10],
            "body": shorten(root.get("body", "")),
            "file": pos.get("new_path") or pos.get("old_path") or None,
            "new_line": pos.get("new_line"),
            "old_line": pos.get("old_line"),
            "is_general": not bool(pos),
            "reply_count": len(replies),
            "replies": [
                {
                    "author": (r.get("author") or {}).get("username", ""),
                    "body": shorten(r.get("body", ""), 2000),
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
