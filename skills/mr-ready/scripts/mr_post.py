#!/usr/bin/env python3
"""Write to a GitLab MR via glab and verify every write by reading it back.

glab quirks this hides: a JSON body needs `-H 'Content-Type: application/json' --input -`
(form data → 415 on PUT, silently dropped `position` on POST); a POST that created no note
still exits 0 with an `id` on stdout — so nothing here trusts an exit code, every subcommand
re-fetches and checks.

    mr_post.py thread --url MR --file a.ts --line 12 --body-file b.md   # new file:line thread
    mr_post.py reply  --url MR --discussion <id>       --body-file b.md   # reply into a thread
    mr_post.py describe --url MR --body-file block.md                     # upsert marked block

Prints one JSON line per call: {"ok": true, "discussion_id": ..., "note_id": ...}.
"""

import argparse
import json
import subprocess
import sys
from urllib.parse import quote, urlparse

MARK_OPEN = "<!-- mr-ready -->"
MARK_CLOSE = "<!-- /mr-ready -->"


def parse_url(url):
    p = urlparse(url)
    marker = "/-/merge_requests/"
    if marker not in p.path:
        sys.exit(f"not an MR url: {url}")
    project, rest = p.path.split(marker, 1)
    iid = rest.strip("/").split("/")[0].split("?")[0]
    return p.netloc, quote(project.strip("/"), safe=""), iid


def api(host, method, endpoint, body=None):
    cmd = ["glab", "api", "--hostname", host, "-X", method, endpoint]
    stdin = None
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--input", "-"]
        stdin = json.dumps(body)
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"glab {method} {endpoint}: {r.stderr.strip()}")
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        sys.exit(f"glab {method} {endpoint}: non-JSON reply: {r.stdout[:200]}")


def discussions(host, proj, iid):
    out, page = [], 1
    while True:
        chunk = api(host, "GET", f"projects/{proj}/merge_requests/{iid}/discussions?per_page=100&page={page}")
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def note_ids(discs, disc_id=None):
    return {n["id"] for d in discs if disc_id is None or d["id"] == disc_id for n in d["notes"]}


def cmd_thread(a, host, proj, iid):
    mr = api(host, "GET", f"projects/{proj}/merge_requests/{iid}")
    refs = mr["diff_refs"]
    body = {
        "body": a.body,
        "position": {
            "position_type": "text",
            "base_sha": refs["base_sha"], "head_sha": refs["head_sha"], "start_sha": refs["start_sha"],
            "new_path": a.file, "old_path": a.file, "new_line": a.line,
        },
    }
    created = api(host, "POST", f"projects/{proj}/merge_requests/{iid}/discussions", body)
    disc_id = created.get("id")
    match = [d for d in discussions(host, proj, iid) if d["id"] == disc_id]
    if not match:
        sys.exit("thread POST returned an id that the discussions listing does not contain")
    pos = (match[0]["notes"][0].get("position") or {})
    if pos.get("new_path") != a.file or pos.get("new_line") != a.line:
        sys.exit(f"thread created but position dropped: got {pos}")
    return {"ok": True, "discussion_id": disc_id, "note_id": match[0]["notes"][0]["id"]}


def cmd_reply(a, host, proj, iid):
    before = note_ids(discussions(host, proj, iid), a.discussion)
    created = api(host, "POST", f"projects/{proj}/merge_requests/{iid}/discussions/{a.discussion}/notes",
                  {"body": a.body})
    after = note_ids(discussions(host, proj, iid), a.discussion)
    new = after - before
    if created.get("id") not in new:
        sys.exit(f"reply POST returned id {created.get('id')} but the thread gained {sorted(new)}")
    return {"ok": True, "discussion_id": a.discussion, "note_id": created["id"]}


def cmd_describe(a, host, proj, iid):
    mr = api(host, "GET", f"projects/{proj}/merge_requests/{iid}")
    desc = mr.get("description") or ""
    # the caller may have pasted the markers into the body — one pair is ours to add
    body = a.body.replace(MARK_OPEN, "").replace(MARK_CLOSE, "").strip()
    block = f"{MARK_OPEN}\n{body}\n{MARK_CLOSE}"
    if MARK_OPEN in desc and MARK_CLOSE in desc:
        head, rest = desc.split(MARK_OPEN, 1)
        _, tail = rest.rsplit(MARK_CLOSE, 1)
        # stray markers left by an earlier bad upsert must not survive
        tail = tail.replace(MARK_OPEN, "").replace(MARK_CLOSE, "")
        desc = head + block + tail
    else:
        desc = (desc.rstrip() + "\n\n" if desc.strip() else "") + block
    api(host, "PUT", f"projects/{proj}/merge_requests/{iid}", {"description": desc})
    back = api(host, "GET", f"projects/{proj}/merge_requests/{iid}").get("description") or ""
    if block not in back:
        sys.exit("description PUT succeeded but the block is not in the re-fetched description")
    return {"ok": True}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["thread", "reply", "describe"])
    p.add_argument("--url", required=True)
    p.add_argument("--body-file", required=True, help="markdown body; a file, so quoting never bites")
    p.add_argument("--file", help="thread: new_path")
    p.add_argument("--line", type=int, help="thread: new_line")
    p.add_argument("--discussion", help="reply: discussion id")
    a = p.parse_args()
    with open(a.body_file, encoding="utf-8") as fh:
        a.body = fh.read().strip()
    if a.cmd == "thread" and not (a.file and a.line):
        p.error("thread needs --file and --line")
    if a.cmd == "reply" and not a.discussion:
        p.error("reply needs --discussion")
    host, proj, iid = parse_url(a.url)
    fn = {"thread": cmd_thread, "reply": cmd_reply, "describe": cmd_describe}[a.cmd]
    print(json.dumps(fn(a, host, proj, iid), ensure_ascii=False))


if __name__ == "__main__":
    main()
