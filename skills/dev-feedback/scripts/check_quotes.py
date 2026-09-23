#!/usr/bin/env python3
"""Гейт цитат Step 4: выбрасывает наблюдения, чьей цитаты нет в диффе/тредах МР.

Вход — JSON-массив ответов батчей Step 3 и субагентов Step 3б (у каждого project_id и iid).
Источник — кэш fetch_mr_details.py в /tmp/dev-feedback/<project_id>-<iid>.json.
"""

import argparse
import json
import os
import re
import sys

CACHE_DIR = "/tmp/dev-feedback"

# поле → где обязана лежать цитата
DIFF, THREAD, ANY = {"diff"}, {"thread"}, {"diff", "thread"}


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def load_sources(pid, iid):
    path = os.path.join(CACHE_DIR, f"{pid}-{iid}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        mr = json.load(f)
    # маркеры +/- в начале строк диффа мешают многострочным цитатам
    diff = "\n".join(
        line[1:] if line[:1] in "+- " else line
        for fc in mr["changed_files"]
        for line in fc["diff"].split("\n")
    )
    thread = "\n".join(c["body"] for c in mr["comments"])
    return {"diff": norm(diff), "thread": norm(thread)}


def claims(item):
    """(контейнер, ключ, допустимые источники) для каждого заякоренного пункта."""
    cq = item.get("code_quality") or {}
    for kind in ("positives", "issues"):
        for i in range(len(cq.get(kind) or [])):
            yield cq[kind], i, DIFF
    rd = item.get("review_dynamics") or {}
    for i in range(len(rd.get("notable_comments") or [])):
        yield rd["notable_comments"], i, THREAD
    for key, where in (("notable", ANY), ("essence", DIFF), ("decision", ANY), ("thinking", THREAD)):
        if item.get(key):
            yield item, key, where


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, help="JSON-массив ответов Step 3 и 3б")
    parser.add_argument("--out", required=True, help="куда записать отфильтрованное")
    args = parser.parse_args()

    with open(args.results) as f:
        items = json.load(f)

    total = kept = 0
    dropped = []
    for item in items:
        src = load_sources(item.get("project_id"), item.get("iid"))
        drop = []
        for container, key, where in claims(item):
            total += 1
            claim = container[key]
            quote = norm(claim.get("quote") if isinstance(claim, dict) else "")
            ok = src is not None and quote and any(quote in src[w] for w in where)
            if ok:
                kept += 1
            else:
                drop.append((container, key))
                dropped.append(f"!{item.get('iid')} {'/'.join(sorted(where))}: {quote[:80] or '<нет цитаты>'}")
        # удаляем с конца, чтобы индексы в списках не съезжали
        for container, key in reversed(drop):
            if isinstance(container, list):
                container.pop(key)
            else:
                container[key] = None

    with open(args.out, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"после гейта осталось {kept} наблюдений из {total}")
    missing = sorted({f"!{i.get('iid')}" for i in items if load_sources(i.get("project_id"), i.get("iid")) is None})
    if missing:
        print(f"нет кэша диффа (все пункты выброшены): {', '.join(missing)}", file=sys.stderr)
    for line in dropped:
        print("  -", line)


if __name__ == "__main__":
    main()
