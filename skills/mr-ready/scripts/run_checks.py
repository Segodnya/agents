#!/usr/bin/env python3
"""Run the repo checks listed in a file, one command per line, without a shell.

    cd <repo-root> && run_checks.py MR_DIR/checks.txt MR_DIR [--timeout 600]   # → MR_DIR/check-<n>.log

No shell — see _lib/run.py for why and for the line format.

Prints one JSON line per command: {"cmd", "exit", "log", "tail"} — `tail` (last 15 log lines)
only for a failed one. A `VAR=val` prefix in a line goes to the env; a tool that must run from a
nested workspace (its resolver keys off cwd) — `/usr/bin/env -C <dir> node_modules/.bin/<tool> …`.
Timeout → "exit": 124.
`jest --findRelatedTests` gets `--passWithNoTests` (no related tests = green).
Exit: 0 all green, 1 some red, 2 usage error / no commands.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
from run import run  # noqa: E402


def usage(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    args, timeout = sys.argv[1:], 600
    if "--timeout" in args:
        i = args.index("--timeout")
        try:
            timeout = int(args[i + 1])
        except (IndexError, ValueError):
            usage("--timeout needs seconds")
        del args[i:i + 2]
    if len(args) != 2:
        usage(__doc__)
    listing, log_dir = args
    os.makedirs(log_dir, exist_ok=True)
    with open(listing, encoding="utf-8") as fh:
        cmds = [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]
    if not cmds:
        usage(f"no commands in {listing}")
    failed = False
    for i, cmd in enumerate(cmds, 1):
        log = f"{log_dir}/check-{i}.log"
        with open(log, "w", encoding="utf-8") as out:
            code, msg = run(cmd, timeout, stdout=out)
            out.write(msg)
        tail = []
        if code:
            failed = True
            with open(log, encoding="utf-8", errors="replace") as fh:
                tail = fh.read().splitlines()[-15:]
        print(json.dumps({"cmd": cmd, "exit": code, "log": log, "tail": tail}, ensure_ascii=False))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
