#!/usr/bin/env python3
"""Write to a GitLab MR via glab and verify every write by reading it back.

glab quirks this hides: a JSON body needs `-H 'Content-Type: application/json' --input -`
(form data → 415 on PUT, silently dropped `position` on POST); a POST that created no note
still exits 0 with an `id` on stdout — so nothing here trusts an exit code, every subcommand
re-fetches and checks.

    mr_post.py thread --url MR --file a.ts --line 12 --body-file b.md   # thread with a hand-written body (line outside the diff → unpositioned)
    mr_post.py reply  --url MR --discussion <id>       --body-file b.md   # reply into a thread (--reopen: unresolve first)
    mr_post.py describe --url MR --body-file block.md                     # upsert marked block
    mr_post.py publish --url MR --mr-dir MR_DIR [--dry-run]              # mr-ready handoff, after push: queued replies + threads for
                                                                          # held L-items from state.json; JSON {replies, threads, skipped, failed}

Prints one JSON line per call: {"ok": true, "discussion_id": ..., "note_id": ...}; an unpositioned
thread adds "general": true.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import api, discussions, mr_endpoint, parse_url  # noqa: E402
import state  # noqa: E402


MARK_OPEN, MARK_CLOSE = state.MARK_OPEN, state.MARK_CLOSE


def discussion(host, proj, iid, disc_id):
    return api(host, "GET", f"{mr_endpoint(proj, iid)}/discussions/{disc_id}")


def old_line_of(base, head, path, line):
    """GitLab takes a line the diff didn't add only with its old_line too — without it: 400 line_code.
    → old_line; None for an added line or when the local repo lacks the refs."""
    r = state.git("diff", "-U0", base, head, "--", path, check=False)
    if r.returncode:
        return None
    delta = 0
    for old_len, c, new_len in re.findall(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", r.stdout, re.M):
        b_len, c, d_len = int(old_len or 1), int(c), int(new_len or 1)
        if d_len and c <= line < c + d_len:
            return None
        if (c + d_len - 1 if d_len else c) < line:
            delta += d_len - b_len
    return line - delta


def unpositioned(host, proj, iid, body):
    created = api(host, "POST", f"{mr_endpoint(proj, iid)}/discussions", {"body": body})
    disc_id = created.get("id")
    back = discussion(host, proj, iid, disc_id) if disc_id else {}
    if not back.get("notes"):
        sys.exit("thread POST returned an id that GitLab does not have")
    return {"ok": True, "discussion_id": disc_id, "note_id": back["notes"][0]["id"], "general": True}


def cmd_thread(a, host, proj, iid, refs=None):
    if not (a.file and a.line):
        return unpositioned(host, proj, iid, a.body)
    refs = refs or api(host, "GET", mr_endpoint(proj, iid))["diff_refs"]
    position = {
        "position_type": "text",
        "base_sha": refs["base_sha"], "head_sha": refs["head_sha"], "start_sha": refs["start_sha"],
        "new_path": a.file, "old_path": a.file, "new_line": a.line,
    }
    old = old_line_of(refs["base_sha"], refs["head_sha"], a.file, a.line)
    if old is not None:
        position["old_line"] = old
    body = {"body": a.body, "position": position}
    created = api(host, "POST", f"{mr_endpoint(proj, iid)}/discussions", body, soft=True)
    # a position error (the line is not in the diff) → unpositioned thread; any other error is fatal, in full
    if re.search(r"line_code|position", created.get("error", ""), re.I):
        print(f"position refused, falling back to unpositioned: {created['error']}", file=sys.stderr)
        # the line is not in the diff, GitLab refuses a positioned thread — keep the place in the body
        where = f"`{a.file}:{a.line}`"
        # after the title: audit-reply spots our findings by the `🤖 self-review ·` prefix
        title, _, rest = a.body.partition("\n")
        text = a.body if where in a.body else f"{title}\n\n{where}\n{rest}"
        return unpositioned(host, proj, iid, text)
    if "error" in created:
        sys.exit(f"thread POST: {created['error']}")
    disc_id = created.get("id")
    back = discussion(host, proj, iid, disc_id) if disc_id else {}
    if not back.get("notes"):
        sys.exit("thread POST returned an id that GitLab does not have")
    note = back["notes"][0]
    pos = note.get("position") or {}
    if pos.get("new_path") != a.file or pos.get("new_line") != a.line:
        sys.exit(f"thread created but position dropped: got {pos}")
    return {"ok": True, "discussion_id": disc_id, "note_id": note["id"]}


def reply(host, proj, iid, disc_id, body):
    created = api(host, "POST", f"{mr_endpoint(proj, iid)}/discussions/{disc_id}/notes", {"body": body})
    ids = {n["id"] for n in discussion(host, proj, iid, disc_id).get("notes", [])}
    if created.get("id") not in ids:
        sys.exit(f"reply POST returned id {created.get('id')} but the thread does not contain it")
    return {"ok": True, "discussion_id": disc_id, "note_id": created["id"]}


def cmd_reply(a, host, proj, iid):
    if a.reopen:
        api(host, "PUT", f"{mr_endpoint(proj, iid)}/discussions/{a.discussion}?resolved=false")
        back = discussion(host, proj, iid, a.discussion)
        if any(n.get("resolved") for n in back.get("notes", []) if n.get("resolvable")):
            sys.exit(f"reopen PUT returned but {a.discussion} is still resolved")
    return reply(host, proj, iid, a.discussion, a.body)


def cmd_describe(a, host, proj, iid):
    mr = api(host, "GET", mr_endpoint(proj, iid))
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
    api(host, "PUT", mr_endpoint(proj, iid), {"description": desc})
    back = api(host, "GET", mr_endpoint(proj, iid)).get("description") or ""
    if block not in back:
        sys.exit("description PUT succeeded but the block is not in the re-fetched description")
    return {"ok": True}


def cmd_publish(a, host, proj, iid):
    """Every write of a mr-ready run, once. state.json is saved after each post: a rerun never posts twice."""
    s = state.load(a.mr_dir)
    src = s["mr"]["source_branch"]
    # a reply cites commits: they must already be on the remote
    if not a.dry_run and state.git("merge-base", "--is-ancestor", "HEAD", f"origin/{src}", check=False).returncode:
        sys.exit(f"HEAD is not on origin/{src}: push and `git fetch origin {src}` first")
    author = s["mr"]["author"]
    out = {"replies": [], "threads": [], "skipped": [], "failed": []}
    queued = {k: it for k, it in s["items"].items() if it["status"] == "queued"}
    discs = {d["id"]: d for d in discussions(host, proj, iid)} if queued else {}
    for did, r in queued.items():
        disc = discs.get(did)
        if disc is None:
            out["failed"].append({"id": did, "reason": "thread not found"})
            continue
        late = [x for x in disc["notes"] if not x.get("system") and x["author"]["username"] != author
                and datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).timestamp() > r["reply"]["at"]]
        if late:
            # the next run's Ground imports the new note and the fixer answers it
            out["skipped"].append({"id": did, "reason": f"new note by @{late[-1]['author']['username']}"})
            continue
        if a.dry_run:
            out["replies"].append({"id": did, "body": r["reply"]["body"], "dry_run": True})
            continue
        try:
            res = reply(host, proj, iid, did, r["reply"]["body"])
        except SystemExit as e:
            out["failed"].append({"id": did, "reason": str(e)})
            continue
        r.update(status="posted", note_id=res["note_id"])
        state.save(a.mr_dir, s)
        out["replies"].append({"id": did, "note_id": res["note_id"]})
    held = {k: it for k, it in s["items"].items()
            if state.local(it) and it["status"] == "open" and it.get("cat") in state.HANDOFF_CATS}
    # diff_refs don't change within a run: one GET for every positioned thread
    refs = api(host, "GET", mr_endpoint(proj, iid))["diff_refs"] if held and not a.dry_run else None
    for lid, it in held.items():
        why = state.HANDOFF_CATS[it["cat"]] + (f": {it['reason']}" if it["cat"] == "reverted" and it["reason"] else "")
        ns = argparse.Namespace(file=it["file"], line=it["line"], body=f"{state.render(it)}\n\n_{why}_")
        if a.dry_run:
            out["threads"].append({"id": lid, "body": ns.body, "dry_run": True})
            continue
        try:
            res = cmd_thread(ns, host, proj, iid, refs)
        except SystemExit as e:
            out["failed"].append({"id": lid, "reason": str(e)})
            continue
        it.update(status="posted", discussion_id=res["discussion_id"])
        state.save(a.mr_dir, s)
        out["threads"].append({"id": lid, "discussion_id": res["discussion_id"]})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["thread", "reply", "describe", "publish"])
    p.add_argument("--url", required=True)
    p.add_argument("--body-file", help="markdown body; a file, so quoting never bites")
    p.add_argument("--file", help="thread: new_path")
    p.add_argument("--line", type=int, help="thread: new_line")
    p.add_argument("--discussion", help="reply: discussion id")
    p.add_argument("--reopen", action="store_true", help="reply: unresolve the thread first")
    p.add_argument("--mr-dir", help="publish: MR_DIR with state.json")
    p.add_argument("--dry-run", action="store_true", help="publish: list what would be posted, post nothing")
    a = p.parse_args()
    if a.cmd == "publish" and not a.mr_dir:
        p.error("publish needs --mr-dir")
    if a.cmd != "publish":
        if not a.body_file:
            p.error(f"{a.cmd} needs --body-file")
        with open(a.body_file, encoding="utf-8") as fh:
            a.body = fh.read().strip()
    if a.cmd == "thread" and not (a.file and a.line):
        p.error("thread needs --file and --line")
    if a.cmd == "reply" and not a.discussion:
        p.error("reply needs --discussion")
    host, proj, iid = parse_url(a.url)
    fn = {"thread": cmd_thread, "reply": cmd_reply, "describe": cmd_describe, "publish": cmd_publish}[a.cmd]
    print(json.dumps(fn(a, host, proj, iid), ensure_ascii=False))


if __name__ == "__main__":
    main()
