#!/usr/bin/env python3
"""Collect the repo's path-scoped rules that match the changed files.

Auto-injection of `paths:` rules fires only on a native `Read` of a matching
file and never inside a subagent — so nothing loads them when the diff is read
with `cat`/`sed` or reviewed by an agent. This does it deterministically.

    git diff --staged --name-only | match_rules.py <repo-root> > RS_DIR/rules.md

Full text of every matching rule to stdout, a one-line summary to stderr.
"""

# Копии абзаца про автоинъекцию в review-staged/SKILL.md и audit-reply/SKILL.md
# намеренные: агент исполняет скилл, а не докстринг.

import os
import re
import sys

RULE_DIRS = [".claude/rules", ".agents/rules", ".cursor/rules"]


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


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: git diff --name-only | match_rules.py <repo-root>")
    repo = sys.argv[1]
    changed = [ln.strip() for ln in sys.stdin if ln.strip()]
    if not changed:
        sys.exit("no changed files on stdin")

    all_rules = rule_files(repo)
    matched, skipped = [], []
    for path in all_rules:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        globs = globs_of(text)
        if globs is None:
            hits = ["<без paths:/globs: — общерепозиторное>"]
        else:
            res = [(g, glob_to_re(g)) for g in globs]
            hits = sorted({g for g, rx in res for f in changed if rx.match(f)})
        rel = os.path.relpath(path, repo)
        (matched if hits else skipped).append((rel, hits, text))

    for rel, hits, text in matched:
        print(f"\n\n===== RULE FILE: {rel} — matched {', '.join(hits)} =====\n")
        print(text)

    sys.stderr.write(
        f"rules: matched {len(matched)} of {len(all_rules)} "
        f"({', '.join(r for r, *_ in matched) or '—'}); "
        f"skipped {', '.join(r for r, *_ in skipped) or '—'}\n"
    )


if __name__ == "__main__":
    main()
