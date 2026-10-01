#!/usr/bin/env python3
"""Mechanical gate over the agents' candidates (review-staged, bugty-hunter). Run from the repo root.

    gate.py DIR --axes A B C D                                   # review-staged
    gate.py DIR --axes state lifecycle contract --map DIR/map.md --require-repro   # bugty-hunter

Reads DIR/axis-<name>.json, DIR/files.txt (the perimeter) and, when present, DIR/checklist.md,
DIR/rules.md, DIR/rule-files.txt. Writes DIR/gate.json and DIR/gate.log, prints the report's
`_Discarded …_` line. Exit 2 — an axis file is missing: that agent hasn't reported yet, wait
for it instead of gating a partial set.

No judgment here: the quote and the rule line are grepped back, a candidate touching no perimeter
file is off-perimeter, `pre_existing` goes to tickets (TKn). An entry chain decides the rest:
without --map a P0/P1 with no chain drops to P2; with --map a chain whose first hop isn't under
`## Входные точки` makes the candidate a suspect (Sn). Reachability through a named guard stays
with the caller.
"""

import argparse
import functools
import json
import os
import re
import sys

REASONS = ["unproven", "refuted", "no evidence", "quote not in file", "rule not in file",
           "off-perimeter", "no repro", "unparseable"]
SEV = {"P0": 0, "P1": 1, "P2": 2}


@functools.cache
def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def find_line(text, needle):
    """1-based numbers of lines containing needle."""
    return [i for i, line in enumerate(text.splitlines(), 1) if needle in line]


def probe(quote):
    """The longest line of the quote: short lines (`}`, `return;`) match anywhere."""
    lines = [ln.strip() for ln in quote.splitlines() if ln.strip()]
    return max(lines, key=len) if lines else ""


def loc_files(c):
    locs = c.get("evidence", {}).get("locations") or []
    if isinstance(locs, str):
        locs = re.split(r"[;,]\s*", locs)
    return [re.sub(r":[\d\-]+$", "", x.strip()) for x in locs if x.strip()]


def entry_of(c):
    return (c.get("evidence") or {}).get("entry") or c.get("entry") or ""


def entry_points(map_path):
    """`file:line` tokens listed under `## Входные точки` in the recon map."""
    text = read(map_path) or ""
    m = re.search(r"^## Входные точки\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return set(re.findall(r"[\w./-]+:\d+", m.group(1))) if m else set()


def check(c, files, rs_dir, a):
    """→ (reason | None, note). Mutates file/line/severity on rebind or downgrade."""
    ev = c.setdefault("evidence", {})
    needle = probe(ev.get("quote") or "")
    if not needle:
        return "no evidence", ""
    hits = find_line(read(c["file"]) or "", needle)
    if not hits:
        other = next((f for f in files if find_line(read(f) or "", needle)), None)
        if not other:
            return "quote not in file", ""
        c["file"], hits = other, find_line(read(other), needle)
    line = int(c.get("line") or 0)
    nearest = min(hits, key=lambda h: abs(h - line))
    note = f"line {line}→{nearest}" if nearest != line else ""
    # the thread is anchored here: the reviewer's number drifts, the quote doesn't
    c["line"] = nearest

    src = c.get("rule_source") or ""
    m = re.search(r"«(.+?)»", src)
    if src.startswith("checklist") and not m:
        return "rule not in file", ""
    if m:
        where = os.path.join(rs_dir, "checklist.md") if src.startswith("checklist") else src.split(":")[0].strip()
        body = read(os.path.expanduser(where)) or read(os.path.join(rs_dir, "rules.md")) or ""
        if m.group(1).strip() not in body:
            return "rule not in file", ""

    if not src.startswith("checklist") and c["file"] not in files and not set(loc_files(c)) & set(files):
        return "off-perimeter", ""
    if a.require_repro and not ((c.get("repro") or {}).get("steps") if isinstance(c.get("repro"), dict) else c.get("repro")):
        return "no repro", ""
    if not a.map and c.get("severity") in ("P0", "P1") and not entry_of(c):
        c["severity"] = "P2"
        note = (note + "; " if note else "") + "downgraded: no entry"
    return None, note


def merge(kept):
    """One defect reported by two axes → one candidate: (file, line ±5)."""
    out = []
    for c in kept:
        twin = next((o for o in out if o["file"] == c["file"] and abs(o["line"] - c["line"]) <= 5), None)
        if not twin:
            out.append(c)
            continue
        if SEV.get(c["severity"], 3) < SEV.get(twin["severity"], 3):
            twin["severity"] = c["severity"]
        if len(c["evidence"].get("quote", "")) > len(twin["evidence"].get("quote", "")):
            twin["evidence"]["quote"] = c["evidence"]["quote"]
        if c["claim"] not in twin["claim"]:
            twin["claim"] += " / " + c["claim"]
        twin["axes"] += c["axes"]
        twin["pre_existing"] = twin.get("pre_existing") and c.get("pre_existing")
        locs = twin["evidence"].get("locations") or []
        twin["evidence"]["locations"] = list(dict.fromkeys(
            (locs if isinstance(locs, list) else [locs]) + (c["evidence"].get("locations") or [])))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("rs_dir")
    p.add_argument("--axes", nargs="+", default=list("ABCD"))
    p.add_argument("--map", help="recon map: route candidates whose entry isn't an entry point to suspects")
    p.add_argument("--require-repro", action="store_true", help="drop candidates without repro steps")
    a = p.parse_args()
    rs = a.rs_dir

    missing = [x for x in a.axes if not os.path.exists(f"{rs}/axis-{x}.json")]
    if missing:
        print(json.dumps({"waiting_for": missing}))
        sys.exit(2)

    files = [f.strip() for f in (read(f"{rs}/files.txt") or "").splitlines() if f.strip()]
    tally = dict.fromkeys(REASONS, 0)
    cands, notes, rules_read, other, log = [], [], {}, [], []
    for x in a.axes:
        try:
            data = json.loads(read(f"{rs}/axis-{x}.json"))
        except (ValueError, TypeError):
            other.append(f"axis {x}: невалидный JSON")
            tally["unparseable"] += 1
            continue
        for k in ("refuted", "unproven"):
            tally[k] += int((data.get("dropped") or {}).get(k) or 0)
        for c in data.get("candidates") or []:
            c["axes"] = x
            cands.append(c)
        notes += [{**n, "axes": x} if isinstance(n, dict) else {"question": n, "axes": x}
                  for n in data.get("design_notes") or []]
        if data.get("rules_read") is not None:
            rules_read[x] = data["rules_read"]

    kept = []
    for i, c in enumerate(cands, 1):
        reason, note = check(c, files, rs, a)
        log.append(f"c{i} · {c.get('axes')} · {c.get('file')}:{c.get('line')} · "
                   f"{'dropped: ' + reason if reason else 'kept'}{' · ' + note if note else ''}")
        if reason:
            tally[reason] += 1
        else:
            kept.append(c)

    kept = sorted(merge(kept), key=lambda c: (SEV.get(c["severity"], 3), c["file"], c["line"]))
    tickets = [c for c in kept if c.get("pre_existing")]
    kept = [c for c in kept if not c.get("pre_existing")]
    if a.map:
        points = entry_points(a.map)
        first_hop = lambda c: entry_of(c).split("→")[0].strip()  # noqa: E731
        suspects = [c for c in kept if first_hop(c) not in points]
        findings = [c for c in kept if first_hop(c) in points]
    else:
        suspects, findings = [], kept
    for prefix, group in (("#", findings), ("TK", tickets), ("S", suspects)):
        for n, c in enumerate(group, 1):
            c["id"] = f"{prefix}{n}"
    for n, d in enumerate(notes, 1):
        d["id"] = f"D{n}"

    expected = [f.strip() for f in (read(f"{rs}/rule-files.txt") or "").splitlines() if f.strip()]
    rules_missing = [f for f in expected if "B" in rules_read and f not in rules_read["B"]]

    discarded = sum(tally.values())
    total = len(cands) + tally["refuted"] + tally["unproven"] + tally["unparseable"]
    parts = ", ".join(f"{v} {k}" for k, v in tally.items() if v)
    line = f"_Discarded {discarded} of {total} candidates{': ' + parts if parts else ''}._"

    with open(f"{rs}/gate.log", "w", encoding="utf-8") as fh:
        fh.write("\n".join(log or ["no candidates"]) + "\n")
    with open(f"{rs}/gate.json", "w", encoding="utf-8") as fh:
        json.dump({"findings": findings, "tickets": tickets, "suspects": suspects, "design_notes": notes, "tally": tally,
                   "discarded_line": line, "rules_read": rules_read, "rules_missing": rules_missing,
                   "other": other}, fh, ensure_ascii=False, indent=1)
    print(line)
    print(json.dumps({"findings": len(findings), "tickets": len(tickets), "suspects": len(suspects), "design_notes": len(notes),
                      "rules_missing": rules_missing, "other": other}, ensure_ascii=False))


if __name__ == "__main__":
    main()
