#!/usr/bin/env python3
"""Collect the repo's path-scoped rules that match the changed files.

Auto-injection of `paths:` rules fires only on a native `Read` of a matching
file and never inside a subagent — so nothing loads them when the diff is read
with `cat`/`sed` or reviewed by an agent. This does it deterministically.

    git diff --staged --name-only | match_rules.py <repo-root> \\
        [--global <dir>]... [--rule-files-out <file>] > RS_DIR/rules.md

Full text of every matching rule to stdout, a one-line summary to stderr.
--rule-files-out: paths of applicable global rule files (each --global dir,
default ~/.claude/rules; *.md, no .bak; `paths:` checked against the diff)
plus ~/.claude/CLAUDE.md and <repo>/CLAUDE.md|AGENTS.md when present.
"""


import os
import re
import sys

RULE_DIRS = [".claude/rules", ".agents/rules", ".cursor/rules"]


def expand_braces(glob):
    """`a.{ts,tsx}` -> [`a.ts`, `a.tsx`]; nested braces by recursion."""
    m = re.search(r"\{([^{}]*)\}", glob)
    if not m:
        return [glob]
    return [r for alt in m.group(1).split(",")
            for r in expand_braces(glob[:m.start()] + alt + glob[m.end():])]


def glob_to_re(glob):
    # `/skills/**` и `skills/` — обычная запись в правилах, `git diff --name-only`
    # отдаёт пути без ведущего слэша и каталоги не отдаёт вовсе.
    glob = glob.lstrip("/")
    if glob.endswith("/"):
        glob += "**"
    out, i = "", 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif c == "*":
            out, i = out + "[^/]*", i + 1
        elif c == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(c), i + 1
    return re.compile(f"^{out}$")


def rule_files(repo):
    seen, found = set(), []
    for rel in RULE_DIRS:
        for root, _, names in os.walk(os.path.join(repo, rel), followlinks=True):
            for name in sorted(names):
                if not name.endswith((".md", ".mdc")):
                    continue
                path = os.path.join(root, name)
                key = os.path.realpath(path)
                if key not in seen:
                    seen.add(key)
                    found.append(path)
    return found


def globs_of(text):
    """`paths:`/`globs:` из фронтматтера; None — ключа нет, правило общерепозиторное."""
    if not text.startswith("---"):
        return None
    head = text.split("---", 2)[1]
    # Якорь на начало строки: подстрока поймала бы `extra_paths:` и `paths:` внутри `description:`.
    key = re.search(r"^[ \t]*(?:paths|globs):", head, re.M)
    if key is None:
        return None
    inline, _, block = head[key.end():].partition("\n")
    inline = inline.strip()
    if inline and not inline.startswith("#"):
        return [g.strip().strip("\"'") for g in inline.strip("[]").split(",") if g.strip()]
    globs = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("- "):
            globs.append(line[2:].strip().strip("\"'"))
        elif line and not line.startswith("#"):
            break
    return globs


def matching_globs(globs, changed):
    res = [(g, glob_to_re(x)) for g in globs for x in expand_braces(g)]
    return sorted({g for g, rx in res for f in changed if rx.match(f)})


def global_rule_files(dirs, repo, changed):
    found = [os.path.expanduser("~/.claude/CLAUDE.md"),
             os.path.join(repo, "CLAUDE.md"), os.path.join(repo, "AGENTS.md")]
    found = [p for p in found if os.path.isfile(p)]
    for d in dirs:
        for root, _, names in os.walk(os.path.expanduser(d), followlinks=True):
            for name in sorted(names):
                if name.endswith(".md") and ".bak" not in name:
                    path = os.path.join(root, name)
                    with open(path, encoding="utf-8") as fh:
                        globs = globs_of(fh.read())
                    if globs is None or matching_globs(globs, changed):
                        found.append(path)
    return found


def main():
    args, gdirs, out = sys.argv[1:], [], None
    while "--global" in args:
        i = args.index("--global")
        gdirs.append(args[i + 1])
        del args[i:i + 2]
    if "--rule-files-out" in args:
        i = args.index("--rule-files-out")
        out = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit("usage: git diff --name-only | match_rules.py <repo-root> "
                 "[--global <dir>]... [--rule-files-out <file>]")
    repo = args[0]
    changed = [ln.strip() for ln in sys.stdin if ln.strip()]
    if not changed:
        sys.exit("no changed files on stdin")

    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("".join(p + "\n" for p in global_rule_files(gdirs or ["~/.claude/rules"], repo, changed)))

    all_rules = rule_files(repo)
    matched, skipped = [], []
    for path in all_rules:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        globs = globs_of(text)
        if globs is None:
            hits = ["<без paths:/globs: — общерепозиторное>"]
        else:
            hits = matching_globs(globs, changed)
        rel = os.path.relpath(path, repo)
        (matched if hits else skipped).append((rel, hits, text))

    for rel, hits, text in matched:
        print(f"\n\n===== RULE FILE: {rel} — matched {', '.join(hits)} =====\n")
        print(text)

    sys.stderr.write(
        f"matched {len(matched)} of {len(all_rules)} "
        f"({', '.join(r for r, *_ in matched) or '—'}); "
        f"skipped {', '.join(r for r, *_ in skipped) or '—'}\n"
    )


if __name__ == "__main__":
    main()
