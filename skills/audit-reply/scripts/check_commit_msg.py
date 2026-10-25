#!/usr/bin/env python3
"""Lint a commit message with the repo's own commitlint before `git commit`, run from the repo root.

    check_commit_msg.py <message-file>

The commit-msg hook runs only after pre-commit, so a bad header costs a whole pre-commit run.
No commitlint binary or no config in the repo → prints `skip`, exit 0: there is no rule to break.
Otherwise exit = commitlint's exit, its output on stdout.
"""

import glob
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from run import run  # noqa: E402

CONFIGS = ("commitlint.config.*", ".commitlintrc", ".commitlintrc.*")


def has_config():
    if any(glob.glob(p) for p in CONFIGS):
        return True
    try:
        with open("package.json", encoding="utf-8") as fh:
            return "commitlint" in json.load(fh)
    except (OSError, ValueError):
        return False


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if not has_config():
        print("skip: no commitlint in this repo")
        return 0
    code, out = run(f"commitlint --edit {shlex.quote(sys.argv[1])}", 120)
    if code == 127:
        print("skip: no commitlint in this repo")
        return 0
    print(out, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
