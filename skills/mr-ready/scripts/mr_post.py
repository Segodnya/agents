#!/usr/bin/env python3
"""Write to a GitLab MR via glab and verify every write by reading it back.

glab quirks this hides: a JSON body needs `-H 'Content-Type: application/json' --input -`
(form data → 415 on PUT, silently dropped `position` on POST); a POST that created no note
still exits 0 with an `id` on stdout — so nothing here trusts an exit code, every subcommand
re-fetches and checks.

    mr_post.py thread --url MR --gate RS_DIR/gate.json --id '#3'          # thread for a gate.json finding (#n / TKn / Dn), body rendered here
    mr_post.py thread --url MR --file a.ts --line 12 --body-file b.md   # thread with a hand-written body (line outside the diff → unpositioned)
    mr_post.py reply  --url MR --discussion <id>       --body-file b.md   # reply into a thread
    mr_post.py describe --url MR --body-file block.md                     # upsert marked block
    mr_post.py stats --url MR [--mr-dir MR_DIR]                           # Handoff counters; writes nothing to GitLab
    mr_post.py reply-all --url MR --replies AR_DIR/replies.json --mr-dir MR_DIR [--round r] [--dry-run]
                                                                          # post every reply (verified_by ran in reply_add.py); JSON {posted, skipped, failed}

Prints one JSON line per call: {"ok": true, "discussion_id": ..., "note_id": ...}; an unpositioned
fallback thread adds "general": true.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import SELF_REVIEW as SELF, api, discussions, mr_endpoint, parse_url  # noqa: E402


MARK_OPEN = "<!-- mr-ready -->"
MARK_CLOSE = "<!-- /mr-ready -->"


def discussion(host, proj, iid, disc_id):
    return api(host, "GET", f"{mr_endpoint(proj, iid)}/discussions/{disc_id}")


def render(f):
    """Thread body for a gate.json entry. The author sees the line it hangs on: no code, no recipe —
    what is wrong, where else, how to reproduce, which rule."""
    ev = f.get("evidence") or {}
    title = f.get("claim") or f.get("question") or ""
    lines = [f"{SELF} · {'Вне скоупа: ' if f['id'].startswith('TK') else ''}{title}"]
    here = f"{f['file']}:{f['line']}"
    others = [x for x in ev.get("locations") or f.get("locations") or [] if x != here]
    if others:
        lines.append("Где ещё: " + ", ".join(others))
    if ev.get("repro"):
        lines.append("repro: " + ev["repro"])
    if f.get("rule_source") and f["rule_source"] != "universal":
        lines.append(f"({f['rule_source']})")
    return "\n\n".join(lines)


def from_gate(a):
    """--gate/--id → a.file, a.line, a.body. A design note has no file/line of its own: its first location."""
    with open(a.gate, encoding="utf-8") as fh:
        g = json.load(fh)
    f = next((x for x in g["findings"] + g["tickets"] + g["design_notes"] if x["id"] == a.id), None)
    if f is None:
        sys.exit(f"{a.id} not in {a.gate}")
    if "file" not in f:
        loc = (f.get("locations") or [""])[0]
        f["file"], _, line = loc.rpartition(":")
        f["line"] = int(re.match(r"\d+", line).group()) if re.match(r"\d+", line) else 0
    if not f["file"] or not f["line"]:
        sys.exit(f"{a.id} has no file:line — post it with glab mr note")
    a.file, a.line, a.body = f["file"], int(f["line"]), render(f)


def old_line_of(base, head, path, line):
    """GitLab takes a line the diff didn't add only with its old_line too — without it: 400 line_code.
    → old_line; None for an added line or when the local repo lacks the refs."""
    r = subprocess.run(["git", "diff", "-U0", base, head, "--", path], capture_output=True, text=True)
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


def cmd_thread(a, host, proj, iid):
    mr = api(host, "GET", mr_endpoint(proj, iid))
    refs = mr["diff_refs"]
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
    is_general = bool(re.search(r"line_code|position", created.get("error", ""), re.I))
    if is_general:
        print(f"position refused, falling back to unpositioned: {created['error']}", file=sys.stderr)
        # the line is not in the diff, GitLab refuses a positioned thread — keep the place in the body
        where = f"`{a.file}:{a.line}`"
        # after the title: audit-reply spots our findings by the `🤖 self-review ·` prefix
        title, _, rest = a.body.partition("\n")
        text = a.body if where in a.body else f"{title}\n\n{where}\n{rest}"
        created = api(host, "POST", f"{mr_endpoint(proj, iid)}/discussions", {"body": text})
    elif "error" in created:
        sys.exit(f"thread POST: {created['error']}")
    disc_id = created.get("id")
    back = discussion(host, proj, iid, disc_id) if disc_id else {}
    if not back.get("notes"):
        sys.exit("thread POST returned an id that GitLab does not have")
    note = back["notes"][0]
    if is_general:
        return {"ok": True, "discussion_id": disc_id, "note_id": note["id"], "general": True}
    pos = note.get("position") or {}
    if pos.get("new_path") != a.file or pos.get("new_line") != a.line:
        sys.exit(f"thread created but position dropped: got {pos}")
    return {"ok": True, "discussion_id": disc_id, "note_id": note["id"]}


def cmd_reply(a, host, proj, iid):
    created = api(host, "POST", f"{mr_endpoint(proj, iid)}/discussions/{a.discussion}/notes",
                  {"body": a.body})
    ids = {n["id"] for n in discussion(host, proj, iid, a.discussion).get("notes", [])}
    if created.get("id") not in ids:
        sys.exit(f"reply POST returned id {created.get('id')} but the thread does not contain it")
    return {"ok": True, "discussion_id": a.discussion, "note_id": created["id"]}


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


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def cmd_stats(a, host, proj, iid):
    """Counters for the Handoff line, over the whole MR: earlier runs wiped MR_DIR, not their threads."""
    mr = api(host, "GET", mr_endpoint(proj, iid))
    repo = os.path.basename(git("rev-parse", "--show-toplevel"))
    # a round = a review-staged dir holding review-<repo>-<iid>.md: MR_DIR/rs-r* plus old /tmp/review-staged-<repo>-*
    dirs = glob.glob(f"{glob.escape(a.mr_dir)}/rs-r*/") if a.mr_dir else []
    dirs += glob.glob(f"/tmp/review-staged-{glob.escape(repo)}-*/")
    stamps = sorted(
        datetime.fromtimestamp(os.path.getmtime(f)).astimezone()
        for d in dirs
        for f in [f"{d}review-{repo}-{iid}.md"]
        if os.path.exists(f)
    )
    # the MR's commits, not HEAD's: the checkout may already sit on another branch
    git("fetch", "-q", "origin", mr["source_branch"], mr["target_branch"])
    commits = git("log", "--format=%H", f"origin/{mr['target_branch']}..origin/{mr['source_branch']}").split()
    ours = [d for d in discussions(host, proj, iid) if d["notes"] and d["notes"][0]["body"].startswith(SELF)]
    n = f = k = v = 0
    for d in ours:
        replies = " ".join(x["body"] for x in d["notes"][1:] if x["body"].startswith(SELF))
        if not replies:
            continue
        k += 1
        n += not all(x.get("resolved") for x in d["notes"] if x.get("resolvable"))
        # a reply cites a short hash of any length git chose; match it as a prefix of the full one
        f += any(c.startswith(h) for h in re.findall(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", replies) for c in commits)
        v += "откатил" in replies.lower()
    first_thread = min((datetime.fromisoformat(d["notes"][0]["created_at"].replace("Z", "+00:00")) for d in ours),
                       default=None)
    # /tmp was cleaned since the first round → the count is a lower bound
    partial = bool(first_thread and (not stamps or first_thread < stamps[0]))
    r = f"≥{len(stamps)}" if partial else str(len(stamps))
    return {"ok": True, "r": r, "n": n, "f": f, "k": k, "v": v, "threads": len(ours)}


def cmd_reply_all(a, host, proj, iid):
    with open(a.replies, encoding="utf-8") as fh:
        replies = json.load(fh)
    built = os.path.getmtime(a.replies)
    mr_author = api(host, "GET", mr_endpoint(proj, iid))["author"]["username"]
    out = {"posted": [], "skipped": [], "failed": []}
    os.makedirs(a.mr_dir, exist_ok=True)
    # verified_by already ran when reply_add.py wrote each entry; one listing serves every reply
    discs = {d["id"]: d for d in discussions(host, proj, iid)}
    for n, rep in enumerate(replies, 1):
        did = rep["discussion_id"]
        disc = discs.get(did)
        if disc is None:
            out["failed"].append({"discussion_id": did, "reason": "thread not found"})
            continue
        late = [x for x in disc["notes"] if not x.get("system") and x["author"]["username"] != mr_author
                and datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).timestamp() > built]
        if late:
            out["skipped"].append({"discussion_id": did, "reason": f"new note by @{late[-1]['author']['username']} after the replies were built"})
            continue
        path = f"{a.mr_dir}/r{a.round}-a{n}.md"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(rep["body"])
        if a.dry_run:
            out["posted"].append({"discussion_id": did, "body_file": path, "dry_run": True})
            continue
        try:
            a.discussion, a.body = did, rep["body"]
            out["posted"].append({**cmd_reply(a, host, proj, iid), "body_file": path})
        except SystemExit as e:
            out["failed"].append({"discussion_id": did, "reason": str(e)})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["thread", "reply", "describe", "stats", "reply-all"])
    p.add_argument("--url", required=True)
    p.add_argument("--body-file", help="markdown body; a file, so quoting never bites")
    p.add_argument("--gate", help="thread: RS_DIR/gate.json")
    p.add_argument("--id", help="thread: #n / TKn / Dn from --gate")
    p.add_argument("--file", help="thread: new_path")
    p.add_argument("--line", type=int, help="thread: new_line")
    p.add_argument("--discussion", help="reply: discussion id")
    p.add_argument("--mr-dir", help="stats: count MR_DIR/rs-r* rounds; reply-all: where bodies go")
    p.add_argument("--replies", help="reply-all: AR_DIR/replies.json")
    p.add_argument("--round", type=int, default=1, help="reply-all: round number for r<r>-a<n>.md")
    p.add_argument("--dry-run", action="store_true", help="reply-all: check threads, post nothing")
    a = p.parse_args()
    if a.cmd == "reply-all" and not (a.replies and a.mr_dir):
        p.error("reply-all needs --replies and --mr-dir")
    if a.cmd == "thread" and a.gate:
        if not a.id:
            p.error("thread --gate needs --id")
        from_gate(a)
    elif a.cmd not in ("stats", "reply-all"):
        if not a.body_file:
            p.error(f"{a.cmd} needs --body-file")
        with open(a.body_file, encoding="utf-8") as fh:
            a.body = fh.read().strip()
    if a.cmd == "thread" and not (a.file and a.line):
        p.error("thread needs --file and --line")
    if a.cmd == "reply" and not a.discussion:
        p.error("reply needs --discussion")
    host, proj, iid = parse_url(a.url)
    fn = {"thread": cmd_thread, "reply": cmd_reply, "describe": cmd_describe, "stats": cmd_stats,
          "reply-all": cmd_reply_all}[a.cmd]
    print(json.dumps(fn(a, host, proj, iid), ensure_ascii=False))


if __name__ == "__main__":
    main()
