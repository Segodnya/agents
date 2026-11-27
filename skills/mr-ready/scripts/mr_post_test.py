#!/usr/bin/env python3
"""Самопроверка mr_post.py: GitLab ответил на POST 500, но заметку создал (MR !32772, 2026-11-17).
Запуск: python3 mr_post_test.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mr_post  # noqa: E402

BODY = "находка"
STORED = {"id": "D1", "notes": [{"id": 7, "body": BODY + "\n", "system": False}]}


def post_500(host, method, endpoint, body=None, soft=False):
    if method == "POST":
        return {"error": "glab: 500 Internal Server Error (HTTP 500)"}
    return STORED


mr_post.api = post_500
mr_post.discussions = lambda host, proj, iid: [STORED]

assert mr_post.unpositioned("h", "p", 1, BODY)["note_id"] == 7, "тред создан при 500 — ok с его note_id"
assert mr_post.reply("h", "p", 1, "D1", BODY)["note_id"] == 7, "ответ создан при 500 — ok с его note_id"
try:
    mr_post.unpositioned("h", "p", 1, "другое тело")
    sys.exit("FAIL: 500 без заметки должен падать")
except SystemExit as e:
    assert "no note with this body" in str(e), e

# Короткий id из индекса read_threads.py: GitLab на него отвечает 404 (MR !32512, 2026-11-17)
FULL = "e5fe994d01294098f61e7401bf2ebf9e6cf5116a"
mr_post.discussions = lambda host, proj, iid: [{"id": FULL}, {"id": "e5fe1111" + "0" * 32}]
assert mr_post.full_discussion_id("h", "p", 1, "e5fe994d") == FULL, "уникальный префикс раскрывается"
assert mr_post.full_discussion_id("h", "p", 1, FULL) == FULL, "полный id — как есть"
for prefix in ("e5fe", "ffff"):
    try:
        mr_post.full_discussion_id("h", "p", 1, prefix)
        sys.exit(f"FAIL: префикс {prefix!r} не уникален или не найден — должен падать")
    except SystemExit as e:
        assert "need exactly one" in str(e), e

# Запуск из другого репо: каждый diff с головой MR падал как настоящее расхождение (MR !32704, 2026-11-18)
here = os.getcwd()
with tempfile.TemporaryDirectory() as tmp:
    os.chdir(tmp)
    try:
        assert not mr_post.is_checkout_of("core/core_backend"), "не git-репо — не чекаут"
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@git.example.com:core/core_backend.git"], check=True)
        assert mr_post.is_checkout_of("core/core_backend"), "ssh-remote проекта MR"
        assert not mr_post.is_checkout_of("core/core_back"), "префикс пути — другой проект"
        assert not mr_post.is_checkout_of("core_backend"), "без группы — другой проект"
        subprocess.run(["git", "remote", "set-url", "origin", "https://git.example.com/core/core_backend"], check=True)
        assert mr_post.is_checkout_of("core/core_backend"), "https-remote без .git"
    finally:
        os.chdir(here)
print("ok")
