#!/usr/bin/env python3
"""Performance-review analyzer for Claude Code usage history.

Reads ~/.claude/history.jsonl (flat user-prompt log) and ~/.claude/projects/*/*.jsonl
(per-session conversation log) over a configurable window and emits a structured JSON
report that captures: volume, length buckets, slash-command distribution, lazy-starter
words, short-nudge approvals, behavior-pattern regex hits, consecutive-duplicate loops,
churn sessions, error/paste prompts, a random sample of medium-length prompts,
tool-use counts, and code-review follow-up heuristics.

Designed to be called once per perf-review run by the perf-review skill.
"""

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from glob import glob
from pathlib import Path

NUDGE_TEXTS = {
    "y", "Y", "Y.", "1", "2", "3", "A", "B", "C", "a", "A.", "B.",
    "Continue", "Continue.", "Go.", "Да.", "да", "Да", "Yes", "yes",
    "Fix it", "Fix all three issues.", "А.", "Б.", "В.",
    "Давай вариант А.",
}

BEHAVIOR_PATTERNS = {
    "imperative_one_word": (
        r"^(fix|сделай|почини|давай|поправь|реши|улучши|переделай|переработай|"
        r"добавь|удали|убери|сократи|объясни|расскажи|проверь|посмотри|обнови|"
        r"сохрани|закоммить|пили|пиши)[\s\.\!]*$"
    ),
    "do_everything": (
        r"(сделай всё|напиши за меня|реализуй полностью|напиши код полностью|"
        r"do it all|just do it)"
    ),
    "frustrated": (
        r"(блин|чёрт|какого|что за|зачем ты|ты опять|снова ты|ёб|долбоёб|"
        r"идиот|тупой|тупо|fuck|wtf|идиотски|опять ты)"
    ),
    "repeat_question": (
        r"(я же говорил|я уже говорил|сколько раз|опять то же|повторяю|"
        r"я тебе уже|снова повторяю)"
    ),
    "vague": (
        r"^(что-то|почему-то|как-то|сделай как-нибудь|разберись)"
    ),
    "no_thinking_error": (
        r"^(не работает|не пашет|ошибка|сломалось|broken|doesn'?t work|"
        r"упал|упало|crashed|fail(ed)?)\.?$"
    ),
}

ERROR_PASTE_RE = re.compile(
    r"(error|Error|TypeError|Cannot|ошибка|сломалось|упал|failed|fail:|broken)",
    re.IGNORECASE,
)

LAZY_STARTERS = (
    "напиши", "сделай", "реализуй", "давай", "просто", "попробуй", "поправь",
    "почему", "что", "как", "разберись", "посмотри",
)

PASTE_ONLY_RE = re.compile(r"^\[Pasted text #\d+ \+\d+ lines\]$")
PASTE_PREFIX_RE = re.compile(r"\[Pasted text #\d+")
SLASH_RE = re.compile(r"^(/[\w\-:]+)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=30,
                   help="Window size in days from the most recent prompt (default: 30).")
    p.add_argument("--history", default=str(Path.home() / ".claude" / "history.jsonl"),
                   help="Path to flat history.jsonl.")
    p.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"),
                   help="Directory containing per-project session JSONL.")
    p.add_argument("--repo-history", action="append", default=[],
                   help="Additional flat history.jsonl files (repeatable).")
    p.add_argument("--top-projects", type=int, default=10)
    p.add_argument("--top-short", type=int, default=30,
                   help="How many top short messages to return.")
    p.add_argument("--top-slash", type=int, default=25)
    p.add_argument("--sample-size", type=int, default=40)
    p.add_argument("--output", default="-",
                   help="Output path or '-' for stdout (default: stdout).")
    return p.parse_args()


def load_flat_history(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def normalize_prompts(rows: list[dict]) -> list[dict]:
    """Coerce flat-history rows into {display, timestamp, project, sessionId}."""
    out = []
    for r in rows:
        ts = r.get("timestamp")
        disp = r.get("display") or r.get("prompt") or ""
        if ts is None or not isinstance(disp, str):
            continue
        out.append({
            "display": disp,
            "timestamp": ts,
            "project": r.get("project") or r.get("projectPath") or "?",
            "sessionId": r.get("sessionId") or r.get("session_id") or "?",
        })
    return out


def within_window(prompts: list[dict], days: int) -> tuple[list[dict], int, int]:
    timestamps = [p["timestamp"] for p in prompts]
    if not timestamps:
        return [], 0, 0
    latest = max(timestamps)
    cutoff = latest - days * 24 * 3600 * 1000
    recent = [p for p in prompts if p["timestamp"] >= cutoff]
    return recent, cutoff, latest


def iter_session_files(projects_dir: str):
    if not projects_dir or not os.path.isdir(projects_dir):
        return
    for path in glob(os.path.join(projects_dir, "*", "*.jsonl")):
        if "/subagents/" in path:
            continue
        yield path


def analyze_sessions(projects_dir: str, cutoff_ts: int) -> dict:
    """Walk per-session JSONL to count tool_use events and assistant turns."""
    tool_counter: Counter[str] = Counter()
    task_spawns = 0
    assistant_turns = 0
    # session_id -> list[(timestamp, had_text_after_codereview)]
    code_review_followups: dict[str, list[tuple[int, str]]] = defaultdict(list)

    for path in iter_session_files(projects_dir):
        try:
            mtime_ms = int(os.path.getmtime(path) * 1000)
        except OSError:
            continue
        if mtime_ms < cutoff_ts:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        session_id = None
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = session_id or obj.get("sessionId")
            ts = obj.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
                except ValueError:
                    ts = None
            if ts is not None and ts < cutoff_ts:
                continue
            t = obj.get("type")
            if t == "assistant":
                assistant_turns += 1
                msg = obj.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_use":
                            name = (c.get("name") or "").lower()
                            tool_counter[name] += 1
                            if name in ("task", "agent"):
                                task_spawns += 1
            elif t == "user":
                msg = obj.get("message") or {}
                content = msg.get("content")
                disp = ""
                if isinstance(content, str):
                    disp = content
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            disp += c.get("text", "")
                if "/review-staged" in disp and session_id:
                    code_review_followups[session_id].append((ts or 0, "invoked"))
            elif t == "tool_use":
                # newer formats may surface tool_use at top level
                name = (obj.get("name") or "").lower()
                if name:
                    tool_counter[name] += 1

    reviews_run = sum(
        sum(1 for _, kind in items if kind == "invoked")
        for items in code_review_followups.values()
    )

    return {
        "assistant_turns": assistant_turns,
        "tool_use_stats": dict(tool_counter),
        "task_spawns": task_spawns,
        "code_reviews_run": reviews_run,
    }


def lengths_stats(values: list[int]) -> dict:
    if not values:
        return {"min": 0, "max": 0, "mean": 0, "median": 0}
    s = sorted(values)
    return {
        "min": s[0],
        "max": s[-1],
        "mean": sum(s) // len(s),
        "median": s[len(s) // 2],
    }


def main() -> int:
    args = parse_args()

    flat_rows: list[dict] = []
    flat_rows.extend(load_flat_history(args.history))
    for extra in args.repo_history:
        flat_rows.extend(load_flat_history(extra))
    prompts = normalize_prompts(flat_rows)

    if not prompts:
        out = {
            "period": {"from": None, "to": None, "days": args.days},
            "totals": {"prompts": 0, "sessions": 0, "projects": 0, "assistant_turns": 0},
            "error": "no prompts found in history sources",
            "sources": {
                "history_exists": os.path.exists(args.history),
                "projects_dir_exists": os.path.isdir(args.projects_dir),
            },
        }
        emit(out, args.output)
        return 0

    recent, cutoff, latest = within_window(prompts, args.days)
    if not recent:
        emit({
            "period": {"from": None, "to": None, "days": args.days},
            "totals": {"prompts": 0, "sessions": 0, "projects": 0, "assistant_turns": 0},
            "error": f"no prompts in last {args.days} days",
        }, args.output)
        return 0

    period_from = datetime.fromtimestamp(cutoff / 1000).strftime("%Y-%m-%d")
    period_to = datetime.fromtimestamp(latest / 1000).strftime("%Y-%m-%d")

    sessions = defaultdict(list)
    for p in recent:
        sessions[p["sessionId"]].append(p)
    projects_counter = Counter(p["project"] for p in recent)
    sessions_per_project: dict[str, set] = defaultdict(set)
    for p in recent:
        sessions_per_project[p["project"]].add(p["sessionId"])

    lengths = [len(p["display"]) for p in recent]
    very_short = sum(1 for l in lengths if l < 30)
    short_30_200 = sum(1 for l in lengths if 30 <= l < 200)
    well_formed = sum(1 for l in lengths if l >= 200)
    naked_paste = sum(1 for p in recent if PASTE_ONLY_RE.match(p["display"].strip()))

    short_msgs = Counter(p["display"].strip() for p in recent if len(p["display"]) < 30)

    slash_counter: Counter[str] = Counter()
    lazy_counter: Counter[str] = Counter()
    nudge_count = 0
    nudge_samples: list[str] = []
    for p in recent:
        disp = p["display"].strip()
        m = SLASH_RE.match(disp)
        if m:
            slash_counter[m.group(1)] += 1
        low = disp.lower()
        for w in LAZY_STARTERS:
            if low.startswith(w):
                lazy_counter[w] += 1
        if disp in NUDGE_TEXTS:
            nudge_count += 1
            if len(nudge_samples) < 12:
                nudge_samples.append(disp)

    behavior_hits: dict[str, dict] = {}
    for name, raw in BEHAVIOR_PATTERNS.items():
        regex = re.compile(raw, re.IGNORECASE)
        matches = [p["display"][:160] for p in recent if regex.search(p["display"])]
        behavior_hits[name] = {"count": len(matches), "samples": matches[:8]}

    loops_count = 0
    loop_samples: list[str] = []
    for sid, msgs in sessions.items():
        if sid == "?":
            continue
        msgs.sort(key=lambda x: x["timestamp"])
        for a, b in zip(msgs, msgs[1:]):
            ad = a["display"].strip()
            bd = b["display"].strip()
            if ad and ad == bd and len(ad) > 5:
                loops_count += 1
                if len(loop_samples) < 12:
                    loop_samples.append(ad[:160])

    prompt_counts = [len(m) for m in sessions.values()]
    avg_pps = sum(prompt_counts) / len(prompt_counts) if prompt_counts else 0
    churn = 0
    for sid, msgs in sessions.items():
        if len(msgs) <= 2 and any(
            "/clear" in m["display"] or "/exit" in m["display"] for m in msgs
        ):
            churn += 1
    churn_pct = round(100 * churn / len(sessions), 1) if sessions else 0

    error_pastes_all = [
        p["display"] for p in recent
        if ERROR_PASTE_RE.search(p["display"]) and len(p["display"]) < 240
    ]

    medium_pool = [
        p["display"].strip() for p in recent
        if 50 <= len(p["display"]) <= 300
        and not p["display"].startswith("/")
        and not PASTE_PREFIX_RE.search(p["display"][:30])
    ]
    random.seed(42)
    sample = random.sample(medium_pool, min(args.sample_size, len(medium_pool)))

    session_data = analyze_sessions(args.projects_dir, cutoff)

    out = {
        "period": {"from": period_from, "to": period_to, "days": args.days},
        "totals": {
            "prompts": len(recent),
            "sessions": len(sessions),
            "projects": len(projects_counter),
            "assistant_turns": session_data["assistant_turns"],
        },
        "lengths": {
            **lengths_stats(lengths),
            "buckets": {
                "very_short_lt30": very_short,
                "short_30_200": short_30_200,
                "well_formed_gte200": well_formed,
                "naked_paste_only": naked_paste,
            },
        },
        "top_short_messages": [
            {"text": t, "count": c}
            for t, c in short_msgs.most_common(args.top_short)
        ],
        "top_projects": [
            {
                "project": proj,
                "prompts": cnt,
                "sessions": len(sessions_per_project[proj]),
            }
            for proj, cnt in projects_counter.most_common(args.top_projects)
        ],
        "slash_commands": [
            {"cmd": c, "count": n}
            for c, n in slash_counter.most_common(args.top_slash)
        ],
        "lazy_starters": dict(lazy_counter.most_common()),
        "nudges": {"count": nudge_count, "samples": nudge_samples},
        "behavior_patterns": behavior_hits,
        "loops": {"consecutive_duplicates": loops_count, "samples": loop_samples},
        "session_stats": {
            "avg_prompts_per_session": round(avg_pps, 2),
            "max_prompts_per_session": max(prompt_counts) if prompt_counts else 0,
            "churn_sessions": churn,
            "churn_pct": churn_pct,
        },
        "error_pastes": {
            "count": len(error_pastes_all),
            "samples": [e[:200] for e in error_pastes_all[:15]],
        },
        "medium_prompts_sample": sample,
        "tool_use_stats": session_data["tool_use_stats"],
        "task_spawns": session_data["task_spawns"],
        "code_reviews_run_in_sessions": session_data["code_reviews_run"],
    }

    emit(out, args.output)
    return 0


def emit(obj: dict, path: str) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path == "-" or not path:
        sys.stdout.write(text + "\n")
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
