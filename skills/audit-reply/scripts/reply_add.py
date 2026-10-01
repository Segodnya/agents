#!/usr/bin/env python3
"""Append one entry to AR_DIR/replies.json (creates the file if missing). Run from the repo root.

    reply_add.py <replies.json> <discussion_id> <FIX|REPLY|REVERTED> <body> [<verified_by cmd>...]

Every verified_by command runs here first, the way a checks.txt line runs (_lib/run.py):
no shell, `VAR=val` prefixes go to the env, a bare binary resolves to node_modules/.bin of the cwd.
A red or unrunnable command → exit 1, nothing appended: the reply would claim what isn't shown.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import SELF_REVIEW  # noqa: E402
from run import run  # noqa: E402

if len(sys.argv) < 5:
    sys.exit(__doc__.strip())
path, disc, kind, body = sys.argv[1:5]
verified_by = sys.argv[5:]
if kind not in ("FIX", "REPLY", "REVERTED"):
    sys.exit(f"kind must be FIX|REPLY|REVERTED, got {kind}")
if not body.startswith(f"{SELF_REVIEW} · "):
    body = f"{SELF_REVIEW} · {body}"
for cmd in verified_by:
    code, out = run(cmd, timeout=300)
    if code:
        tail = "\n".join(out.strip().splitlines()[-5:])
        sys.exit(f"verified_by failed (exit {code}): {cmd}\n{tail}")
try:
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
except FileNotFoundError:
    items = []
items.append({"discussion_id": disc, "kind": kind, "body": body, "verified_by": verified_by})
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(items, fh, ensure_ascii=False, indent=1)
os.replace(tmp, path)
print(json.dumps({"ok": True, "count": len(items)}))
