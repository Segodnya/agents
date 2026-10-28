#!/usr/bin/env python3
"""mr-ready's only memory: MR_DIR/state.json. Every change goes through this script. Run from the repo root.

    state.py init     --mr-dir MR_DIR --url MR --fetch <fetch_mr.py>   # Ground: resume or fresh, import MR threads once
    state.py add      --mr-dir MR_DIR --round r --gate RS_DIR/gate.json --id '#1' '#3=L2' 'TK1'
                                                                       # new L<n>; `=<id>` — repeats L2 or a MR thread
    state.py add      --mr-dir MR_DIR --round r --claim "checks red: …"  # a finding with no gate entry
    state.py fold     --mr-dir MR_DIR --round r --replies AR_DIR/replies.json [--discuss AR_DIR/discuss.md]
    state.py park     --mr-dir MR_DIR --id L3 --question "…"           # product fork → the final question
    state.py decide   --mr-dir MR_DIR --id L3 --choice fix|reply|defer [--text "…"]
    state.py mark     --mr-dir MR_DIR --id L3 --cat cap|stuck|minor
    state.py settle   --mr-dir MR_DIR --text "решение · почему" [--replace S2 | --drop S2]
    state.py threads  --mr-dir MR_DIR --for review|fix --out FILE      # fetch_mr.py shape for review-staged / audit-reply --threads
                                                                       # --for review also prints `since`: HEAD before the last fix round
    state.py describe --mr-dir MR_DIR --summary-file F --out FILE      # the block for mr_post.py describe
                                                                       # rejected items whose code is gone at HEAD are left out
    state.py show     --mr-dir MR_DIR                                  # compact listing for the orchestrator

An item is `L<n>` (origin local: a gate.json #n / TKn / Dn or a --claim) or a GitLab discussion_id (origin gitlab:
a thread imported at Ground — people's and earlier `🤖 self-review`). A fixed L-item reaches the reviewer as a thread
with the fixer's «Поправил»: a new finding that would undo that fix is then visible as one.
Prints one JSON line per call; add and fold also print `counts`.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from glab_mr import SELF_REVIEW as SELF  # noqa: E402

MARK_OPEN = "<!-- mr-ready -->"
MARK_CLOSE = "<!-- /mr-ready -->"
# render() puts a quoted rule as a `(… «…»)` line; a REPLY against it is the user's call
RULE_LINE = re.compile(r"^\(.*«.+»\)$", re.M)
SECTION = re.compile(r"^### (.+)$", re.M)
# rounds in a row whose findings are all P2 / design notes: the loop has reached polish, the rest is the human's
MINOR_STREAK = 3
# held-for-the-reviewer categories: an open L-item with one of these gets a thread at handoff
HANDOFF_CATS = {"stuck": "две правки не закрыли", "reverted": "правка откачена",
                "cap": "не дошли: лимит раундов", "deferred": "отложено до ревьюера",
                "minor": f"{MINOR_STREAK} раунда подряд без P0/P1"}
REJECTED = "Отклонено self-review"
# answer on a parked item's thread: review-staged closes a repeat of the claim against it
PENDING = "Ждёт решения человека"


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


def path(mr_dir):
    return os.path.join(mr_dir, "state.json")


def load(mr_dir):
    with open(path(mr_dir), encoding="utf-8") as fh:
        return json.load(fh)


def save(mr_dir, s):
    tmp = path(mr_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path(mr_dir))


def head():
    return git("rev-parse", "HEAD").stdout.strip()


def file_at_head(file):
    r = git("show", f"HEAD:{file}", check=False)
    return None if r.returncode else r.stdout


def anchor_of(file, line, quote):
    """Code the item points at, to tell later whether a fix removed it: the quote, else the anchored line."""
    if quote:
        return next((ln.strip() for ln in quote.splitlines() if ln.strip()), None)
    content = file_at_head(file) if file and line else None
    lines = content.splitlines() if content else []
    return lines[line - 1].strip() or None if line and 0 < line <= len(lines) else None


def superseded(it):
    """The anchored file or code is gone at HEAD: a later fix replaced what the item was about."""
    if not it.get("file"):
        return False
    content = file_at_head(it["file"])
    return content is None or bool(it.get("anchor")) and it["anchor"] not in content


def where(it):
    return f"{it['file']}:{it['line']}" if it.get("file") else "general"


def local(it):
    return it["origin"] == "local"


def actionable(it):
    """Still the fixer's: open, not held for the reviewer, and for a MR thread — the last word isn't the author's
    or the user decided FIX on it."""
    if it["status"] != "open" or it.get("cat"):
        return False
    if local(it):
        return True
    # the user is often the MR author: their own «надо поправить» ends with their word too
    return not it["thread"]["resolved"] and (it.get("decision") == "fix" or not it["thread"]["last_by_author"])


def new_local(s, **fields):
    lid = f"L{s['next_l']}"
    s["next_l"] += 1
    s["items"][lid] = {"origin": "local", "kind": "finding", "file": None, "line": None, "locations": [], "repro": None,
                       "rule": False, "status": "open", "cat": None, "fixes": 0, "commits": [], "reason": "", **fields}
    return lid


def reject(s, it, reason):
    it.update(status="rejected", reason=reason)
    if not it.get("rid"):
        it["rid"] = f"R{s['next_r']}"
        s["next_r"] += 1


def resolve(s, key):
    """An L-id is whole (L3 is a prefix of L31); a GitLab id may come cut to 8 chars."""
    if key in s["items"]:
        return key
    hits = [k for k in s["items"] if not k.startswith("L") and k.startswith(key)]
    return hits[0] if len(hits) == 1 else None


def get(s, key):
    k = resolve(s, key)
    return (k, s["items"][k]) if k else sys.exit(f"{key} not in state")


def sections(desc):
    """`### Title` → lines of our marked block, so a hand edit of the description flows back on Ground."""
    if MARK_OPEN not in desc or MARK_CLOSE not in desc:
        return {}
    block = desc.split(MARK_OPEN, 1)[1].rsplit(MARK_CLOSE, 1)[0]
    parts = SECTION.split(block)
    return {parts[i].strip(): [x for x in parts[i + 1].splitlines() if x.strip()] for i in range(1, len(parts), 2)}


def parse_ids(lines, prefix):
    return {m.group(1): m.group(2).strip() for x in lines if (m := re.match(rf"^- ({prefix}\d+) · (.+)$", x.strip()))}


def settled_block(s):
    return "### Settled\n" + "\n".join(f"- {k} · {v}" for k, v in s["settled"].items())


def cmd_init(a):
    os.makedirs(a.mr_dir, exist_ok=True)
    out = {"ok": True, "resumed": False, "archived": None, "conflicts": []}
    old = None
    if os.path.exists(path(a.mr_dir)):
        old = load(a.mr_dir)
        if git("merge-base", "--is-ancestor", old["head"], "HEAD", check=False).returncode:
            # history was rewritten: the log's lines and commits no longer exist
            out["archived"] = f"state.{int(time.time())}.json"
            os.replace(path(a.mr_dir), os.path.join(a.mr_dir, out["archived"]))
            old = None
    r = subprocess.run([sys.executable, a.fetch, "--url", a.url, "--all"], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"fetch_mr.py: {r.stderr.strip()}")
    mr = json.loads(r.stdout)
    threads = mr.pop("threads")
    desc = mr.get("description") or ""
    sec = sections(desc)
    s = old or {"url": a.url, "items": {}, "settled": {}, "next_l": 1, "next_r": 1, "round": 0}
    s["mr"], s["head"] = mr, head()
    # what is outside our block is the author's — threads.json shows it untouched
    mr["description"] = re.sub(re.escape(MARK_OPEN) + r".*?" + re.escape(MARK_CLOSE), "", desc, flags=re.S).strip()
    for sid, text in parse_ids(sec.get("Settled", []), "S").items():
        if s["settled"].get(sid, text) != text:
            out["conflicts"].append({"id": sid, "description": text, "state": s["settled"][sid]})
        else:
            s["settled"][sid] = text
    out["resumed"] = bool(old)
    if not old:
        # another machine or an archived state: the description is the only record of the agent's rejections;
        # a line is `[`file:line` · ]claim[ · reason]`, as cmd_describe writes it
        for rid, text in parse_ids(sec.get(REJECTED, []), "R").items():
            m = re.match(r"^`([^`]+):(\d+)` · (.*)$", text)
            claim, _, reason = (m.group(3) if m else text).rpartition(" · ")
            lid = new_local(s, claim=claim or reason, file=m and m.group(1), line=m and int(m.group(2)))
            s["items"][lid].update(status="rejected", reason=reason if claim else "", rid=rid)
            s["next_r"] = max(s["next_r"], int(rid[1:]) + 1)
    for t in threads:
        prev = s["items"].get(t["discussion_id"], {})
        # a posted reply is in the fresh copy now; a queued one and the user's decisions survive the re-import
        status = "open" if prev.get("status", "posted") == "posted" else prev["status"]
        s["items"][t["discussion_id"]] = {"cat": None, **prev, "origin": "gitlab", "thread": t, "status": status,
                                          "rule": bool(RULE_LINE.search(t["body"]))}
    save(a.mr_dir, s)
    return out


def gate_entry(g, gid, top):
    f = next((x for x in g["findings"] + g["tickets"] + g["design_notes"] if x["id"] == gid), None)
    if f is None:
        sys.exit(f"{gid} not in gate.json")

    def rel(p):
        return os.path.relpath(p, top) if p and os.path.isabs(p) else p

    ev = f.get("evidence") or {}
    locs = [rel(x) for x in ev.get("locations") or f.get("locations") or []]
    file, line = rel(f.get("file")), f.get("line")
    if not file and locs:
        file, _, ln = locs[0].rpartition(":")
        m = re.match(r"\d+", ln)
        line = int(m.group()) if m else None
    rule = f.get("rule_source")
    line = int(line) if line else None
    return {"kind": {"TK": "ticket", "D": "note"}.get(re.match(r"[A-Z]*", gid.lstrip("#")).group(), "finding"),
            "claim": f.get("claim") or f.get("question") or "", "file": file or None, "line": line,
            "anchor": anchor_of(file, line, ev.get("quote")),
            "locations": locs, "repro": ev.get("repro"), "severity": f.get("severity"),
            "rule_source": rule, "rule": bool(rule and rule != "universal")}


def mark_serious(s, rnd):
    """The round brought a P0/P1 or a red check: the minor streak restarts after it."""
    s["last_serious"] = max(s.get("last_serious", 0), rnd)


def cmd_add(a):
    s = load(a.mr_dir)
    out = {"ok": True, "added": [], "reopened": [], "stuck": [], "parked": []}
    s["round"] = max(s["round"], a.round)
    if a.claim:
        out["added"].append(new_local(s, claim=a.claim))
        mark_serious(s, a.round)
        save(a.mr_dir, s)
        return {**out, "counts": counts(s)}
    with open(a.gate, encoding="utf-8") as fh:
        g = json.load(fh)
    top = git("rev-parse", "--show-toplevel").stdout.strip()
    for spec in a.id:
        gid, _, target = spec.partition("=")
        f = gate_entry(g, gid, top)
        if f["severity"] in ("P0", "P1"):
            mark_serious(s, a.round)
        if not target:
            lid = new_local(s, **f)
            it = s["items"][lid]
            # a ticket is out of scope; a design note goes to the fixer, who takes only a contract-neutral cut
            if f["kind"] == "ticket":
                it["status"] = "ticket"
            out["added"].append(lid)
            continue
        key, it = get(s, target)
        if not local(it):
            # the thread is still in work, unless its «Поправил» waits for the push while the finding is back
            if it["status"] == "queued" and it["reply"]["kind"] == "FIX":
                it.update(status="parked", reply=None, question=f"ревьюер снова нашёл: {f['claim']}")
                out["parked"].append(key)
            continue
        if it["status"] not in ("fixed", "rejected"):
            continue
        # the same finding back after a fix: the fix was incomplete; after two it is the human's
        stuck = it["status"] == "fixed" and it["fixes"] >= 2
        it.update(f, status="open", cat="stuck" if stuck else None)
        out["stuck" if stuck else "reopened"].append(key)
    save(a.mr_dir, s)
    return {**out, "counts": counts(s)}


def park(it, question):
    it.update(status="parked", question=question)


def cmd_fold(a):
    """Fixer's results of round r → state. A REPLY that would argue with a quoted rule becomes a fork."""
    s = load(a.mr_dir)
    # a round with only DISCUSS leaves no replies.json
    replies = []
    if os.path.exists(a.replies) and os.path.getsize(a.replies):
        with open(a.replies, encoding="utf-8") as fh:
            replies = json.load(fh)
    out = {"ok": True, "unknown": [], "unanswered": []}
    now = time.time()
    # what threads --for fix handed the fixer: each must come back as a reply or a discuss line
    handed = [k for k, it in s["items"].items() if actionable(it)]
    answered = set()
    for rep in replies:
        key, kind, body = resolve(s, rep["discussion_id"]), rep["kind"], rep["body"]
        if key is None:
            out["unknown"].append(rep["discussion_id"])
            continue
        answered.add(key)
        it = s["items"][key]
        if kind == "REPLY" and it["rule"] and not it.get("decision"):
            park(it, f"REPLY против правила: {body}")
        elif not local(it):
            it.update(status="queued", reply={"kind": kind, "body": body, "at": now}, decision=None)
        elif kind == "FIX":
            it.update(status="fixed", cat=None, decision=None, fix=body, fixed_in=a.round)
            it["fixes"] += 1
            it["commits"] += re.findall(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", body)
        elif kind == "REVERTED":
            it.update(status="open", cat="reverted", reason=body)
        else:
            reject(s, it, body)
    if a.discuss and os.path.exists(a.discuss):
        with open(a.discuss, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("- "):
                    continue
                # one line may carry several threads: `- T4 · L31 · … (+ T5 · L32 · …)`
                ids = re.findall(r"\bT\d+ · (\S+) · ", line)
                question = re.sub(r"^- T\d+ · \S+ · ", "", line)
                for tid in ids:
                    key = resolve(s, tid)
                    if key:
                        park(s["items"][key], question)
                        answered.add(key)
                    else:
                        out["unknown"].append(tid)
    out["unanswered"] = [k for k in handed if k not in answered]
    s["head"], s["round"] = head(), max(s["round"], a.round)
    save(a.mr_dir, s)
    return {**out, "counts": counts(s)}


def cmd_park(a):
    s = load(a.mr_dir)
    key, it = get(s, a.id)
    park(it, a.question)
    save(a.mr_dir, s)
    return {"ok": True, "id": key}


def cmd_decide(a):
    s = load(a.mr_dir)
    key, it = get(s, a.id)
    it["decision"] = a.choice
    if a.choice == "fix":
        # back to the fixer with the user's words; the next prompt names it so DISCUSS triggers stay off
        it.update(status="open", cat=None, fix_text=a.text or "")
    elif a.choice == "defer":
        it.update(status="open", cat="deferred")
    elif not a.text:
        sys.exit("--choice reply needs --text")
    elif local(it):
        reject(s, it, a.text)
    else:
        it.update(status="queued", reply={"kind": "REPLY", "body": a.text, "at": time.time()})
    save(a.mr_dir, s)
    return {"ok": True, "id": key, "status": it["status"]}


def cmd_mark(a):
    s = load(a.mr_dir)
    key, it = get(s, a.id)
    it.update(status="open", cat=a.cat)
    save(a.mr_dir, s)
    return {"ok": True, "id": key}


def cmd_settle(a):
    s = load(a.mr_dir)
    if a.drop:
        s["settled"].pop(a.drop, None) or sys.exit(f"{a.drop} not in Settled")
        sid = a.drop
    else:
        if not a.text:
            sys.exit("settle needs --text")
        sid = a.replace or f"S{max([int(k[1:]) for k in s['settled']] + [0]) + 1}"
        s["settled"][sid] = a.text
    save(a.mr_dir, s)
    return {"ok": True, "id": sid}


def render(it):
    """Thread body for an L-item: what is wrong, where else, how to reproduce, which rule — no code."""
    tag = {"ticket": "Вне скоупа: ", "note": "Design note: "}.get(it["kind"], "")
    lines = [f"{SELF} · {tag}{it['claim']}"]
    others = [x for x in it["locations"] if x != where(it)]
    if others:
        lines.append("Где ещё: " + ", ".join(others))
    if it.get("repro"):
        lines.append("repro: " + it["repro"])
    if it["rule"]:
        lines.append(f"({it['rule_source']})")
    return "\n\n".join(lines)


def as_thread(key, it, author):
    pending = f"{PENDING}: {it['question']}" if it["status"] == "parked" else None
    if local(it):
        answer = {"rejected": it["reason"] or "Отклонено.", "ticket": "Вне скоупа: черновик тикета.",
                  "fixed": f"Поправил в r{it.get('fixed_in', '?')}: {it.get('fix') or ', '.join(it['commits'])}",
                  "parked": pending}.get(it["status"])
        reply = [{"author": author, "body": answer, "created_at": ""}] if answer else []
        return {"discussion_id": key, "note_id": None, "resolved": False, "author": author, "is_author_self": True,
                "last_by_author": bool(answer), "created_at": "", "body": render(it), "file": it["file"],
                "new_line": it["line"], "old_line": None, "is_general": not it["file"], "reply_count": len(reply),
                "replies": reply}
    t = dict(it["thread"])
    body = it["reply"]["body"] if it["status"] == "queued" else pending
    if body:
        t["replies"] = t["replies"] + [{"author": author, "body": body, "created_at": ""}]
        t["reply_count"], t["last_by_author"] = len(t["replies"]), True
    return t


def cmd_threads(a):
    s = load(a.mr_dir)
    author = s["mr"]["author"]
    keep = actionable if a.for_ == "fix" else (lambda it: it["status"] != "posted")
    out = [as_thread(k, it, author) for k, it in s["items"].items() if keep(it)]
    desc = s["mr"]["description"]
    if s["settled"]:
        desc += f"\n\n{MARK_OPEN}\n{settled_block(s)}\n{MARK_CLOSE}"
    data = {**s["mr"], "description": desc, "open_thread_count": sum(not t["resolved"] for t in out), "threads": out}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    res = {"ok": True, "threads": len(out)}
    if a.for_ == "review":
        res["since"] = fixed_since(a.mr_dir, s["round"])
    return res


def fixed_since(mr_dir, rnd):
    """HEAD before the last fix round, for the reviewer's `<since>..HEAD`; None when that round had no fix step.
    Read here so the orchestrator copies a hash instead of recalling one."""
    head = os.path.join(mr_dir, f"head-r{rnd}.txt")
    if not os.path.exists(head):
        return None
    with open(head, encoding="utf-8") as fh:
        sha = fh.read().strip()
    return sha if git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False).returncode == 0 else None


def counts(s):
    items = s["items"].values()
    return {"round": s["round"], "open": sum(map(actionable, items)),
            "forks": sum(it["status"] == "parked" for it in items),
            "minor": s["round"] - s.get("last_serious", 0) >= MINOR_STREAK}


def cmd_describe(a):
    s = load(a.mr_dir)
    with open(a.summary_file, encoding="utf-8") as fh:
        parts = [f"### Summary\n{fh.read().strip()}"]
    if s["settled"]:
        parts.append(settled_block(s))
    items = [it for it in s["items"].values() if local(it)]
    rej = sorted((it for it in items if it["status"] == "rejected" and not superseded(it)),
                 key=lambda it: int(it["rid"][1:]))
    if rej:
        parts.append(f"### {REJECTED}\n" + "\n".join(
            f"- {it['rid']} · " + (f"`{where(it)}` · " if it["file"] else "") + it["claim"]
            + (f" · {it['reason']}" if it["reason"] else "") for it in rej))
    n = {k: sum(it["status"] == k for it in items) for k in ("fixed", "rejected")}
    notes = sum(it["kind"] == "note" and it["status"] == "rejected" for it in items)
    held = sum(it.get("cat") in HANDOFF_CATS for it in items)
    deferred = sum(it.get("cat") == "deferred" for it in s["items"].values())
    parts.append(f"### Self-review\n{s['round']} раундов · поправлено: {n['fixed']} · отклонено: {n['rejected'] - notes} · "
                 f"design notes: {notes} · на ревьюера: {held} · отложено: {deferred}")
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(parts) + "\n")
    return {"ok": True}


def cmd_show(a):
    s = load(a.mr_dir)
    rows, log = [], []
    for k, it in s["items"].items():
        if local(it) and it["status"] == "fixed":
            log.append(f"{k} · {where(it)} · {it['claim'][:80]} · {','.join(it['commits'])} · fixes={it['fixes']}")
            continue
        if not local(it) and it["status"] == "open" and not it.get("cat") and not actionable(it):
            continue
        st = it["status"] + (f"/{it['cat']}" if it.get("cat") else "") + \
            (f"/{it['reply']['kind']}" if it["status"] == "queued" else "")
        gist = it["claim"] if local(it) else f"@{it['thread']['author']} · " + re.sub(r"\s+", " ", it["thread"]["body"])
        loc = where(it) if local(it) else where({"file": it["thread"]["file"], "line": it["thread"]["new_line"]})
        rows.append(f"{k[:8]} · {st} · {loc} · {gist[:100]}"
                    + (f" · ? {it['question']}" if it["status"] == "parked" else "")
                    + (f" · решение: FIX — {it['fix_text']}" if it.get("decision") == "fix" and it["status"] == "open" else ""))
    return {"ok": True, "counts": counts(s), "items": rows, "log": log}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mr-dir", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, *args):
        sp = sub.add_parser(name, parents=[common])
        for flag, kw in args:
            sp.add_argument(flag, **kw)

    req = {"required": True}
    cmd("init", ("--url", req), ("--fetch", {**req, "help": "audit-reply/scripts/fetch_mr.py"}))
    cmd("add", ("--round", {**req, "type": int}), ("--gate", {}), ("--id", {"nargs": "+"}), ("--claim", {}))
    cmd("fold", ("--round", {**req, "type": int}), ("--replies", req), ("--discuss", {}))
    cmd("park", ("--id", req), ("--question", req))
    cmd("decide", ("--id", req), ("--choice", {**req, "choices": ["fix", "reply", "defer"]}), ("--text", {}))
    cmd("mark", ("--id", req), ("--cat", {**req, "choices": ["cap", "stuck", "minor"]}))
    cmd("settle", ("--text", {}), ("--replace", {}), ("--drop", {}))
    cmd("threads", ("--for", {**req, "dest": "for_", "choices": ["review", "fix"]}), ("--out", req))
    cmd("describe", ("--summary-file", req), ("--out", req))
    cmd("show")
    a = p.parse_args()
    if a.cmd == "add" and not (a.claim or (a.gate and a.id)):
        p.error("add needs --gate and --id, or --claim")
    print(json.dumps(globals()[f"cmd_{a.cmd}"](a), ensure_ascii=False))


if __name__ == "__main__":
    main()
