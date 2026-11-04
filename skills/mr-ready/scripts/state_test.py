#!/usr/bin/env python3
"""Самопроверка state.py на граничных записях gate.json: находка без строки, без файла, неизвестный id.
Запуск из корня любого git-репо: python3 state_test.py"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import state  # noqa: E402

top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
gate = {
    "findings": [
        {"id": "#1", "file": "README.md", "line": None, "claim": "файл без строки", "severity": "P2"},
        {"id": "#2", "claim": "без файла и строки", "severity": "P2", "evidence": {"locations": []}},
        {"id": "#3", "file": "README.md", "line": "1", "claim": "строка строкой", "severity": "P2"},
    ],
    "tickets": [],
    "design_notes": [{"id": "D1", "file": "README.md", "line": 3, "question": "заметка"}],
}

fails = []

no_line = state.gate_entry(gate, "#1", top)
if no_line["line"] is not None:
    fails.append(f"#1: line должен быть None, а {no_line['line']!r}")

no_file = state.gate_entry(gate, "#2", top)
if no_file["file"] is not None or no_file["line"] is not None:
    fails.append(f"#2: file/line должны быть None, а {no_file['file']!r}/{no_file['line']!r}")

if state.gate_entry(gate, "#3", top)["line"] != 1:
    fails.append("#3: строковый line не приведён к int")

if state.gate_entry(gate, "D1", top)["kind"] != "note":
    fails.append("D1: kind должен быть note")

try:
    state.gate_entry(gate, "TK1", top)
    fails.append("TK1: неизвестный id не остановил скрипт")
except SystemExit as e:
    if "#3 README.md:1" not in str(e):
        fails.append(f"TK1: в ошибке нет списка id для сверки по file:line: {e}")

with tempfile.TemporaryDirectory() as mr_dir:
    state.save(mr_dir, {"items": {
        "L1": {"origin": "local", "status": "open", "claim": "старый"},
        "L2": {"origin": "local", "status": "posted", "claim": "в MR"},
        "abc": {"origin": "gitlab", "status": "open", "claim": "тред человека"},
    }})
    state.cmd_restate(argparse.Namespace(mr_dir=mr_dir, id="L1", claim="новый"))
    if state.load(mr_dir)["items"]["L1"]["claim"] != "новый":
        fails.append("restate: L1 не получил новый текст")
    for key in ("L2", "abc"):
        try:
            state.cmd_restate(argparse.Namespace(mr_dir=mr_dir, id=key, claim="x"))
            fails.append(f"restate: {key} нельзя переписать")
        except SystemExit:
            pass

print("\n".join(fails) or "ok")
sys.exit(1 if fails else 0)
