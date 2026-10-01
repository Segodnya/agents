#!/usr/bin/env python3
"""Jira comment (ADF) <-> markdown for the deploy checklist, and a pointwise patch back.

    jira_checklist.py get --key K --comment ID --out-md checklist.md --out-adf checklist.adf.json
    jira_checklist.py put --key K --comment ID --adf checklist.adf.json --old checklist.orig.md \\
                          --new checklist.md [--dry-run]

`get` renders paragraph / heading / ordered+bullet lists (nested) / text with marks code, strong, em,
link / hardBreak / inlineCard; decoration marks (textColor, backgroundColor, underline) carry no checklist
meaning and render as plain text; any other node or mark → exit 1 naming its type (nothing else is dropped silently).
`put` diffs list items of --old vs --new: changed item → its text nodes are rewritten, added item → a new
listItem. Deleted items, changes outside list items, or anything ambiguous → exit 1, no guessing.
Before the PUT the comment is re-read and must equal the ADF saved by `get`; after it, re-read and
compared with what was sent. --dry-run prints the plan and writes nothing.

Auth: $JIRA_API_TOKEN + `login` / `server` from ~/.config/.jira/.config.yml.
"""

import argparse
import copy
import difflib
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from jira import auth as jira_auth  # noqa: E402

LISTS = ("orderedList", "bulletList")
# presentation only: a checklist item means the same with or without them
DECOR = ("textColor", "backgroundColor", "underline")
INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)]+)\)")
ITEM = re.compile(r"^(\s*)(\d+\.|-) (.*)$")


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


# ---- Jira ----

def endpoint(key, cid):
    server, auth = jira_auth()
    return f"{server}/rest/api/3/issue/{key}/comment/{cid}", auth


def call(key, cid, method="GET", body=None):
    url, auth = endpoint(key, cid)
    req = urllib.request.Request(url, method=method, headers={"Authorization": auth, "Accept": "application/json",
                                                              "Content-Type": "application/json"},
                                 data=json.dumps({"body": body}).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["body"]
    except Exception as e:
        die(f"{method} {url}: {e}")


# ---- ADF -> markdown ----

def inline(node):
    t = node["type"]
    if t == "hardBreak":
        return "  \n"
    if t == "inlineCard":
        return f"<{node['attrs']['url']}>"
    if t != "text":
        die(f"unsupported ADF node: {t}")
    s = node["text"]
    for m in node.get("marks", []):
        mt = m["type"]
        if mt == "code":
            s = f"`{s}`"
        elif mt == "strong":
            s = f"**{s}**"
        elif mt == "em":
            s = f"*{s}*"
        elif mt == "link":
            s = f"[{s}]({m['attrs']['href']})"
        elif mt not in DECOR:
            die(f"unsupported ADF mark: {mt}")
    return s


def para(node):
    return "".join(inline(c) for c in node.get("content", []))


def items_of(lst, depth=0, up=None, out=None):
    """Flat list of list items in document order: {depth, li, parent, up, p}."""
    out = [] if out is None else out
    for li in lst["content"]:
        if li["type"] != "listItem":
            die(f"unsupported ADF node in list: {li['type']}")
        paras = [c for c in li["content"] if c["type"] == "paragraph"]
        if len(paras) != 1 or li["content"][0]["type"] != "paragraph":
            die("unsupported listItem layout (needs exactly one leading paragraph, then nested lists)")
        rec = {"depth": depth, "li": li, "parent": lst, "up": up, "p": paras[0]}
        out.append(rec)
        for c in li["content"][1:]:
            if c["type"] not in LISTS:
                die(f"unsupported ADF node in listItem: {c['type']}")
            items_of(c, depth + 1, rec, out)
    return out


def render_list(lst, depth=0):
    lines = []
    for n, li in enumerate(lst["content"]):
        marker = f"{lst.get('attrs', {}).get('order', 1) + n}." if lst["type"] == "orderedList" else "-"
        pad = " " * (len(marker) + 1)
        lines.append("   " * depth + marker + " " + para(li["content"][0]).replace("\n", "\n" + "   " * depth + pad))
        for c in li["content"][1:]:
            lines += render_list(c, depth + 1)
    return lines


def to_md(adf):
    blocks = []
    for n in adf["content"]:
        t = n["type"]
        if t == "paragraph":
            blocks.append(para(n))
        elif t == "heading":
            blocks.append("#" * n["attrs"]["level"] + " " + para(n))
        elif t in LISTS:
            items_of(n)  # validates every node before rendering
            blocks.append("\n".join(render_list(n)))
        else:
            die(f"unsupported ADF node: {t}")
    return "\n\n".join(blocks) + "\n"


# ---- markdown -> items ----

def parse_md(md):
    """→ (items [(depth, text)], outside lines). Depth by indent stack; non-item indented lines continue the item."""
    items, outside, stack = [], [], []
    for line in md.splitlines():
        m = ITEM.match(line)
        if m:
            w = len(m.group(1))
            while stack and stack[-1] >= w:
                stack.pop()
            stack.append(w)
            items.append([len(stack) - 1, m.group(3).rstrip()])
        elif line.strip() and line[0] in " \t" and items:
            items[-1][1] += "\n" + line.strip()
        elif line.strip():
            outside.append(line.strip())
            stack = []
    return [tuple(i) for i in items], outside


def inline_nodes(text):
    out, pos = [], 0

    def add(s, marks=None):
        if s:
            out.append({"type": "text", "text": s, **({"marks": marks} if marks else {})})

    def plain(s):
        for k, chunk in enumerate(s.split("\n")):
            if k:
                out.append({"type": "hardBreak"})
            add(chunk)

    for m in INLINE.finditer(text):
        plain(text[pos:m.start()])
        code, strong, em, label, href = m.groups()
        if code:
            add(code, [{"type": "code"}])
        elif strong:
            add(strong, [{"type": "strong"}])
        elif em:
            add(em, [{"type": "em"}])
        else:
            add(label, [{"type": "link", "attrs": {"href": href}}])
        pos = m.end()
    plain(text[pos:])
    return out


# ---- patch ----

def plan_patch(adf, old_md, new_md):
    """→ (plan lines, patched ADF). Exits 1 on anything that is not a plain edit or insert of list items."""
    adf = copy.deepcopy(adf)
    recs = [r for n in adf["content"] if n["type"] in LISTS for r in items_of(n)]
    old_items, old_out = parse_md(old_md)
    new_items, new_out = parse_md(new_md)
    if old_items != [(r["depth"], para(r["p"])) for r in recs]:
        die("--old does not match the ADF (list items differ) — re-run `get`")
    if old_out != new_out:
        die("changes outside list items are not supported")
    plan = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_items, new_items, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op == "delete" or (op == "replace" and i2 - i1 != j2 - j1):
            die(f"ambiguous change at old items {i1 + 1}-{i2} (deletion or unequal replace) — edit by hand")
        if op == "replace":
            for r, (d, text) in zip(recs[i1:i2], new_items[j1:j2]):
                if d != r["depth"]:
                    die(f"item depth changed: {text[:60]!r}")
                if any(c["type"] != "text" for c in r["p"].get("content", [])):
                    die(f"item has non-text nodes (hardBreak/card), edit by hand: {para(r['p'])[:60]!r}")
                if any(m["type"] in DECOR for c in r["p"]["content"] for m in c.get("marks", [])):
                    die(f"item is decorated, a rewrite would drop the decoration — edit by hand: {para(r['p'])[:60]!r}")
                r["p"]["content"] = inline_nodes(text)
                plan.append(f"edit   {'  ' * d}{text[:100]}")
        else:  # insert
            if i1 == 0:
                die("insert before the first item is ambiguous")
            prev = recs[i1 - 1]
            for d, text in new_items[j1:j2]:
                li = {"type": "listItem", "content": [{"type": "paragraph", "content": inline_nodes(text)}]}
                if d == prev["depth"] + 1:
                    nested = [c for c in prev["li"]["content"][1:] if c["type"] in LISTS]
                    if not nested:
                        die(f"no nested list under the previous item for: {text[:60]!r}")
                    parent, rec_up = nested[-1], prev
                    pos = len(parent["content"])
                elif d <= prev["depth"]:
                    anchor = prev
                    while anchor["depth"] > d:
                        anchor = anchor["up"]
                    parent, rec_up = anchor["parent"], anchor["up"]
                    pos = next(k for k, c in enumerate(parent["content"]) if c is anchor["li"]) + 1
                else:
                    die(f"item depth jumps by more than one: {text[:60]!r}")
                parent["content"].insert(pos, li)
                prev = {"depth": d, "li": li, "parent": parent, "up": rec_up, "p": li["content"][0]}
                plan.append(f"insert {'  ' * d}{text[:100]}")
    return plan, adf


def strip_ids(n):
    """Jira adds localIds on save; they are not part of what we sent."""
    if isinstance(n, dict):
        n = {k: strip_ids(v) for k, v in n.items()}
        if isinstance(n.get("attrs"), dict):
            n["attrs"].pop("localId", None)
            if not n["attrs"]:
                del n["attrs"]
        return n
    return [strip_ids(x) for x in n] if isinstance(n, list) else n


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["get", "put"])
    p.add_argument("--key", required=True)
    p.add_argument("--comment", required=True)
    p.add_argument("--out-md")
    p.add_argument("--out-adf")
    p.add_argument("--adf")
    p.add_argument("--old")
    p.add_argument("--new")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.cmd == "get":
        if not (a.out_md and a.out_adf):
            p.error("get needs --out-md and --out-adf")
        adf = call(a.key, a.comment)
        md = to_md(adf)
        open(a.out_md, "w", encoding="utf-8").write(md)
        json.dump(adf, open(a.out_adf, "w", encoding="utf-8"), ensure_ascii=False)
        print(json.dumps({"ok": True, "md": a.out_md, "adf": a.out_adf, "lines": md.count("\n")}))
        return
    if not (a.adf and a.old and a.new):
        p.error("put needs --adf, --old and --new")
    saved = json.load(open(a.adf, encoding="utf-8"))
    fresh = call(a.key, a.comment)
    if fresh != saved:
        die("the Jira comment changed since `get` — re-run `get` and redo the edit")
    plan, patched = plan_patch(saved, open(a.old, encoding="utf-8").read(), open(a.new, encoding="utf-8").read())
    if not plan:
        die("no list-item changes between --old and --new")
    print("\n".join(plan))
    if a.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "changes": len(plan)}))
        return
    call(a.key, a.comment, "PUT", patched)
    back = call(a.key, a.comment)
    if strip_ids(back) != strip_ids(patched):
        die("PUT done, but the re-read comment differs from what was sent — check it in Jira")
    print(json.dumps({"ok": True, "changes": len(plan)}))


if __name__ == "__main__":
    main()
