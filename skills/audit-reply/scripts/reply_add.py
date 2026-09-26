#!/usr/bin/env python3
"""Append one entry to AR_DIR/replies.json (creates the file if missing).

    reply_add.py <replies.json> <discussion_id> <FIX|REPLY|REVERTED> <body>
"""
import json
import sys

path, disc, kind, body = sys.argv[1:5]
if kind not in ("FIX", "REPLY", "REVERTED"):
    sys.exit(f"kind must be FIX|REPLY|REVERTED, got {kind}")
if not body.startswith("🤖 self-review · "):
    body = "🤖 self-review · " + body
try:
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
except FileNotFoundError:
    items = []
items.append({"discussion_id": disc, "kind": kind, "body": body})
with open(path, "w", encoding="utf-8") as fh:
    json.dump(items, fh, ensure_ascii=False, indent=1)
print(json.dumps({"ok": True, "count": len(items)}))
