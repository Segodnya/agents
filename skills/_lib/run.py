"""Run one command line without a shell — the way checks.txt and verified_by lines are run.

No shell on purpose: `for c in "tsc -b"; do node_modules/.bin/$c; done` in zsh does not word-split,
and every check dies with exit 127 while looking like a red run.
"""
import os
import shlex
import subprocess


def run(cmd, timeout, stdout=None):
    """→ (exit, output); output is "" when stdout is a file handle.
    `VAR=val` prefixes go to the env; a bare `vitest` resolves to `node_modules/.bin/vitest` of the cwd
    (not on PATH outside npm scripts); missing binary → 127, timeout → 124."""
    argv, env = shlex.split(cmd), dict(os.environ)
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
