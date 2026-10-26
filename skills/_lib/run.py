"""Run one command line without a shell — the way checks.txt and verified_by lines are run.

No shell on purpose: `for c in "tsc -b"; do node_modules/.bin/$c; done` in zsh does not word-split,
and every check dies with exit 127 while looking like a red run.
˝"""
import os
import re
import shlex
import subprocess

DIFF_TOKEN = re.compile(r"^\{diff:(?P<ref>[^:}]+):(?P<exts>[^}]+)\}$")


def diff_files(ref, exts):
    """Files present at HEAD and on disk that changed since the merge-base with `ref`, by extension."""
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=d", f"{ref}...HEAD"],
                         capture_output=True, text=True, check=True).stdout.split()
    suffixes = tuple("." + e.strip().lstrip(".") for e in exts.split(","))
    # a fixer runs checks before committing a move: HEAD still lists the old path, the disk no longer has it
    return [f for f in out if f.endswith(suffixes) and os.path.exists(f)]


def run(cmd, timeout, stdout=None):
    """→ (exit, output); output is "" when stdout is a file handle.
    `VAR=val` prefixes go to the env; a bare `vitest` resolves to `node_modules/.bin/vitest` of the cwd
    (not on PATH outside npm scripts); missing binary → 127, timeout → 124.
    A `{diff:<ref>:<ext,ext>}` token expands to the diff's files with those extensions; none → the
    command is skipped with exit 0."""
    argv, env = shlex.split(cmd), dict(os.environ)
    expanded = []
    for arg in argv:
        m = DIFF_TOKEN.match(arg)
        if not m:
            expanded.append(arg)
            continue
        files = diff_files(m["ref"], m["exts"])
        if not files:
            note = f"skipped: no {m['exts']} files in the diff against {m['ref']}\n"
            if stdout:
                stdout.write(note)
            return 0, "" if stdout else note
        expanded.extend(files)
    argv = expanded
    while argv and "=" in argv[0] and not argv[0].startswith(("-", "=")):
        k, _, v = argv.pop(0).partition("=")
        env[k] = v
    local_bin = os.path.join("node_modules", ".bin", argv[0]) if argv and "/" not in argv[0] else ""
    if local_bin and os.path.isfile(local_bin):
        argv[0] = local_bin
    if "--findRelatedTests" in argv and "--passWithNoTests" not in argv:
        argv.append("--passWithNoTests")
    try:
        r = subprocess.run(argv, stdout=stdout or subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           env=env, timeout=timeout)
        return r.returncode, r.stdout or ""
    except FileNotFoundError as e:
        return 127, f"{e}\n"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s\n"
