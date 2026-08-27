#!/usr/bin/env python3
"""Read fetch_mr.py output without dumping it into context.

Two phases. The index is cheap and always safe to print; then the verbatim
text of the threads that actually matter — full bodies, never truncated,
because the shape of a fix is dictated in the tail of a thread, not the head.

    read_threads.py threads.json          # index: T1..Tn, file:line, author, gist
    read_threads.py threads.json 3 7      # full text of T3 and T7
"""

import json
import re
import sys


def where(t):
    if not t.get("file"):
        return "general"
    return f"{t['file']}:{t.get('new_line') or t.get('old_line') or '-'}"


def state(t):
    return "resolved" if t.get("resolved") else "OPEN"


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: read_threads.py <threads.json> [N ...]")

    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    threads = data.get("threads", [])
    wanted = [int(a) for a in sys.argv[2:]]

    if not wanted:
        opened = sum(1 for t in threads if not t.get("resolved"))
        print(f"MR !{data.get('iid')} «{data.get('title', '')}» — "
              f"{len(threads)} threads ({opened} open)")
        for i, t in enumerate(threads, 1):
            gist = re.sub(r"\s+", " ", t.get("body", "")).strip()[:140]
            size = len(t.get("body", "")) + sum(len(r.get("body", "")) for r in t.get("replies", []))
            print(f"T{i} {where(t)} @{t['author']} {state(t)} "
                  f"r={t['reply_count']} [{size}c] | {gist}")
        return

    for i in wanted:
        if not 1 <= i <= len(threads):
            print(f"=== T{i} — no such thread (have 1..{len(threads)})\n")
            continue
        t = threads[i - 1]
        print(f"=== T{i} · {where(t)} · @{t['author']} · {state(t)} · {t['created_at']}")
        print(t.get("body", ""))
        for r in t.get("replies", []):
            print(f"--- reply @{r['author']} · {r['created_at']}")
            print(r.get("body", ""))
        print()


if __name__ == "__main__":
    main()
