#!/usr/bin/env python3
"""Самопроверка токена {diff:…} на временном git-репо: запуск python3 run_test.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run import run  # noqa: E402


def git(*args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


fails = []
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "base")
    Path("frontend/js").mkdir(parents=True)
    Path("frontend/js/a.ts").write_text("x")
    Path("backend.ts").write_text("x")
    git("add", ".")
    git("commit", "-q", "-m", "change")

    _, out = run("/bin/echo {diff:HEAD~1:ts}", 10)
    if sorted(out.split()) != ["backend.ts", "frontend/js/a.ts"]:
        fails.append(f"без директории: {out!r}")

    code, out = run("/usr/bin/env -C frontend /bin/ls {diff:HEAD~1:ts:frontend/}", 10)
    if (code, out.strip()) != (0, "js/a.ts"):
        fails.append(f"с директорией: {code} {out!r}")

    code, out = run("/bin/echo {diff:HEAD~1:ts:backend}", 10)
    if (code, out.startswith("skipped")) != (0, True):
        fails.append(f"пустая директория: {code} {out!r}")

print("\n".join(fails) or "ok")
sys.exit(1 if fails else 0)
