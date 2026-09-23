#!/usr/bin/env python3
"""Самопроверка check_quotes.py: цитата кода — только из диффа, из треда — только из тредов,
без кэша МР выбрасывается всё. Запуск: python3 test_check_quotes.py"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_quotes  # noqa: E402

tmp = Path(tempfile.mkdtemp())
check_quotes.CACHE_DIR = str(tmp)
(tmp / "1-10.json").write_text(json.dumps({
    "changed_files": [{"diff": "@@ -1 +1,2 @@\n+export const isActive = (todo) =>\n+  todo.done;\n"}],
    "comments": [{"body": "а почему не List с фильтром?"}],
}))

items = [{
    "project_id": 1, "iid": 10,
    "code_quality": {
        "positives": [{"claim": "ok", "quote": "export const isActive = (todo) => todo.done;"}],  # многострочная
        "issues": [{"claim": "цитата ревьюера вместо кода", "quote": "почему не List с фильтром"}],
    },
    "review_dynamics": {"notable_comments": [{"text": "ok", "quote": "а почему не List с фильтром?"},
                                             {"text": "выдумка", "quote": "добавь тесты"}]},
    "notable": {"text": "без цитаты", "quote": ""},
    "thinking": {"text": "ok", "quote": "почему не List"},
}, {
    "project_id": 1, "iid": 99,  # кэша нет
    "code_quality": {"positives": [{"claim": "x", "quote": "export const isActive"}], "issues": []},
}]

src, out = tmp / "in.json", tmp / "out.json"
src.write_text(json.dumps(items, ensure_ascii=False))
sys.argv = ["check_quotes.py", "--results", str(src), "--out", str(out)]
check_quotes.main()

got = json.loads(out.read_text())
a, b = got
assert len(a["code_quality"]["positives"]) == 1, "многострочная цитата из диффа проходит"
assert a["code_quality"]["issues"] == [], "цитата треда в code_quality не проходит"
assert [c["text"] for c in a["review_dynamics"]["notable_comments"]] == ["ok"]
assert a["notable"] is None, "пустая цитата выбрасывается"
assert a["thinking"] is not None
assert b["code_quality"]["positives"] == [], "без кэша выбрасывается всё"
print("ok")
