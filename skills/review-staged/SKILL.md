---
name: review-staged
description: "Read-only review of a git diff (staged / last commit / branch / worktree) on four axes — correctness & contracts, rules & smells, performance, deploy checklist — where every finding is proven by a verbatim quote and MR-settled ones are dropped. Not the built-in `/code-review`. Use on «ревью стейджа», `/review-staged`, or a request to audit a diff."
---

# review-staged

**Purpose:** find every real problem the diff introduces and report only what is proven by a quote from the real file. Two failure modes to avoid: a missed defect (costs a whole later round) and an unproven claim (costs the author's trust).

```
1 GROUND    diff · rules · MR threads · checklist · checks → RS_DIR
2 REVIEW    4 sonnet reviewers in parallel, one axis each → RS_DIR/axis-X.json
3 GATE      _lib/gate.py → gate.json (quote grepped back, dedup, tally)
4 THREADS   drop what the MR discussion already settled
5 REPORT    chat + RS_DIR/review-<repo>-<iid|mode>.md
```

Invocation: `review-staged [staged|last|branch|worktree] [--no-mr] [--no-spec] [--base <branch>] [--mr <url>] [--checklist <file>] [--rs-dir <path>] [--checks <file>] [--threads <file>] [--design <dir>]`. Value flags answer step-1 questions up front.

`SKILL_DIR` — the path from the harness line `Base directory for this skill:`. Shell state doesn't survive between `Bash` calls: substitute `RS_DIR` and `SKILL_DIR` as literals in every command.

## 1. Ground

| Mode | Range |
| --- | --- |
| `staged` | `git diff --staged` |
| `last` | `git diff HEAD~1..HEAD` |
| `branch` | `git diff $(git merge-base HEAD origin/<base>)..HEAD` |
| `worktree` | `git diff HEAD` |

No mode → ask. `<base>` = `--base`, else `origin/HEAD`'s branch, else `master`; `git fetch origin <base>` first. `RS_DIR` = `--rs-dir` (`mkdir -p`), else a fresh `/tmp/review-staged-<repo>-<branch>-<timestamp>`.

```bash
git diff <range> --name-only > RS_DIR/files.txt     # empty → stop: «нет изменений»
git diff <range> > RS_DIR/diff
git diff <range> --shortstat > RS_DIR/shortstat.txt
git rev-parse HEAD > RS_DIR/head.txt
```

`files.txt` is the only source of paths — `--stat` abbreviates long paths into ones that don't exist. Drop what a tool wrote rather than a human: lock files, build output, minified bundles, snapshots, codegen, vendored deps, binaries.

**Rules** — path-scoped rules don't reach subagents on their own (auto-injection fires only on a native `Read`, never inside a subagent), so collect them here:

```bash
python3 "SKILL_DIR/scripts/match_rules.py" <repo-root> --rule-files-out RS_DIR/rule-files.txt < RS_DIR/files.txt > RS_DIR/rules.md
```

stderr `matched <k> of <n>` goes into the report header verbatim.

**MR** — `--mr`, else `glab mr list --source-branch <branch>` and confirm; neither and no `--no-mr` → ask. Threads to a file, read in step 4:
`python3 "SKILL_DIR/../audit-reply/scripts/fetch_mr.py" --url "<MR>" --all > RS_DIR/threads.json` (`--all`: resolved threads are exactly «уже обсудили»).
`--threads <file>` — the caller already holds the threads in the same shape: `cp` it to `RS_DIR/threads.json`, no GitLab call.

**Checklist** — `--checklist <file>` → `cp` to `RS_DIR/checklist.md`; a Jira key or URL → `python3 "SKILL_DIR/../mr-ready/scripts/jira_checklist.py" get --key <KEY> --out-md RS_DIR/checklist.md` (a `focusedCommentId` in the URL → add `--comment <id>`), exit 1 → ask; else the user pastes it and you save it verbatim; `--no-spec` → none. Reviewers read the file; a checklist retold in your words is how a reviewer ends up quoting a rule nobody wrote.

**Design** — `--design <dir>`: mockup screenshots the caller saved, `*.png` named by Figma node. Only D gets the path.

**Checks** — the list goes to `RS_DIR/checks.txt`, one command per line, the format of the `run_checks.py` docstring:
- `--checks <file>` → `cp` it.
- Else the cache `~/.claude/checks/<origin>.txt` (`<origin>` = `basename -s .git "$(git remote get-url origin)"`, one name for every checkout), its `<ref>` replaced by the range's base: `HEAD` for `staged`/`worktree`, `HEAD~1` for `last`, `origin/<base>` for `branch`.
- No cache → write it: typecheck, lint on the diff's files, related tests — each in the form the repo itself runs it. Its agent rules (`CLAUDE.md` / `AGENTS.md`) or its check CLI (`bin/agent-cli`, a `make` target) name the runtime or wrapper (`bunx --bun vitest`, `node --experimental-vm-modules …/jest`) → use that form. Otherwise binaries with the flags of the package's `scripts` entry. A test run outside the repo's runtime can go red on its own (ICU, module resolution). Files as `{diff:<ref>:ts,tsx}`; a package in a subdirectory → `/usr/bin/env -C <dir> node_modules/.bin/<tool> … {diff:<ref>:ts,tsx:<dir>}`.

Run from the repo root: `python3 "SKILL_DIR/../mr-ready/scripts/run_checks.py" RS_DIR/checks.txt RS_DIR`. Exit 127 on a cached line → the line is stale: fix it. A list you wrote or fixed that runs with no 127 → `mkdir -p ~/.claude/checks`, save it there with the ref written back as `<ref>`. Red is a finding for the header, not a failure. Pass the results to reviewers as one line, so they don't rerun them.

## 2. Review

Spawn four `Agent`s in one message (three under `--no-spec`), each `model: "sonnet"`, `effort: "medium"`. Prompt:

> `RS_DIR = <literal>`. Axis `<X>`. First `cat "<SKILL_DIR>/prelude.md"` and follow it. Checklist: `RS_DIR/checklist.md` (omit under `--no-spec`). Checks already run: <cmd → exit; …>. Findings language: <the MR's / the user's>. <Среда line, if the caller gave one.> <Axis charter below.> <Caller's extra focus, if any — as an addition to the full axis, never instead of it.>

B also gets the literal paths `RS_DIR/rules.md` and `RS_DIR/rule-files.txt`. Threads and previous reports never go into a prompt — reviewers judge the code, step 4 judges the discussion.

**A · Correctness & contracts** — null/undefined, off-by-one, races, unhandled rejections, type holes; security at trust boundaries; empty/huge/odd inputs; a changed signature or payload with a caller not updated (quote both); a key or name that must match another artifact (i18n catalog, config, route, schema, feature flag) changed on one side only, or hard-coded where the repo looks it up; tests that assert less than they claim. Config and docs files of the diff are contracts too (Dockerfile, CI, compose, env samples, README): an env name, version pin, default or documented behaviour that disagrees with the code or with another of them.

**B · Rules & smells** — read `rules.md` whole (by `===== RULE FILE` sections) and every file in `rule-files.txt`; cite a rule as `<file>: «<line verbatim>»`; list what you read in `rules_read`. Fowler smells (P2 unless they bite). One value read from two sources (`a || b`, `a ?? b`, a stored field plus a re-read of URL / store / DOM) is a finding: name both sources and the input where they disagree. A hunk the task does not need — imports or formatting rewritten by a bulk autofix, reordering in a file otherwise touched on one line — is a P2 quoted at the hunk: scope noise hides the real change. Up to 3 design notes, each a question on a named hunk: needed at all? / adds work that wasn't there? / simpler path?

**C · Performance** — each P0/P1 names the delta (`O(n·m) → O(n+m)`, `+1 request → reuse`): nested lookups, work in loops, sequential awaits, redundant requests, work repeated per call / render / event that its inputs don't require.

**D · Spec** — checklist vs diff, three ways, each finding's `kind` naming its way: `absent` — promised but absent (P0 if «fixed», P1 if «touched»); `contradicted` — the diff does otherwise than the item says (priority as absent: a defect in the code, not a checklist edit); `unpromised` — in the diff but not promised: a behaviour change or a runtime step (restart, migration, cache purge) the tester won't know to check (P1). Done when every visible change in the diff (class, token, size, colour, text, markup element) and every runtime step maps to a checklist item or to one finding — one exhaustive pass, so the next round finds nothing new. An absence is quoted at the place it should be. With `--design`: `Read` every screenshot; each element the diff renders (control type, icon, colour, size, state) that differs from the mockup is a P1 finding of `kind: mockup` quoted at the code line, the screenshot named in the claim.

After the spawn message **end your turn with one plain line `жду оси: A B C D`** (this line overrides any rule against status-only turns) — not `SubagentHandback`, which would end your run with no report. Each reviewer's report wakes you: run the gate; exit 2 (`waiting_for`) → end the turn with `жду оси: <missing>`, nothing else.

## 3. Gate

```bash
python3 "SKILL_DIR/../_lib/gate.py" RS_DIR --axes A B C D --require-coverage     # A B C under --no-spec; run from the repo root
```

Your only judgment: a P0/P1 whose entry chain names a guard — open the guard; it makes the line unreachable → P2 or drop, note it on `_Прочее:_`. `rules_missing` non-empty → `SendMessage` B once with those paths; `files_missing` non-empty → `SendMessage` each listed axis once with its paths. Then rerun the gate. `other` → `_Прочее:_`.

## 4. Threads

Skipped under `--no-mr`. Index, then full bodies of every thread on a finding's file and every general thread, in one call, unsliced — the shape of the fix lives in the tail:

```bash
python3 "SKILL_DIR/../audit-reply/scripts/read_threads.py" RS_DIR/threads.json          # index
python3 "SKILL_DIR/../audit-reply/scripts/read_threads.py" RS_DIR/threads.json 3 7 12   # bodies
```

Match by the same code — the `code @<sha> L<n>:` line under a thread's header is its code at its own revision, compare it with the finding's quote — or by the same claim; line numbers drift between revisions. `🤖 self-review ·` roots are earlier mr-ready findings: judge them as a reviewer's threads. Each case, with one line why:

- The answer really closes the finding → `✅ снято тредом T<n>`, the author's quote.
- The answer accepts the cost instead of removing it («ожидаемо», «пусть так», a `S<n>` on the same claim), and the end user sees the cost — behaviour differs from the base branch → the finding stays at its priority, marked `⚠︎ риск принят автором в T<n>`, the quote. A design note on it becomes this finding. Accepting a user-visible regression is the human reviewer's call; this case wins over the `✅`/dropped cases below.
- The answer misses the case — a «поправил» whose fix left this line as it was included → the finding stays.
- A design note any thread already argued → dropped.
- «Поправил в r<n>: …» — a fix already made. A P2 or design note that takes it back (restores what it removed, removes what it added, rewords the same comment again) → `↩︎ отменяет правку T<n>`, the fix quoted, counted like `✅ снято`. A P0/P1 the fix introduced → open as `регрессия правки T<n>`.
- «Ждёт решения человека: …» — a fork already put to the user: closes a finding on the same claim → `✅ снято тредом T<n>`.
- `S<n> · …` under `### Settled` in `description` of `threads.json` — the author's answer with no position: covers a finding or design note by claim → `✅ снято тредом S<n>`, the line quoted.

## 5. Report

Full text to `RS_DIR/review-<repo>-<iid|mode>.md`; the chat gets it minus P2 and ticket bodies.

A finding with a `kind` goes to the 📋 block, every other one to P0–P2. Inside 📋 — one subsection per `kind` that has findings, each ordered P0 → P2, priority on the line. Ids stay the gate's `#n` in both places: `mr-ready` looks a finding up by it.

````markdown
# Code Review — <N> находок (по чек-листу <j>; <M> снято тредами) · вне скоупа: <k> · MR !<iid>

_Треды: <T> (открытых <O>)_ · _Чек-лист: <task> #<comment>, updated <updated> (из JSON `get`) | принят из файла | не предоставлен_ · _Applied rules: <matched k of n> · прочитаны: <rules_read basenames>_
<_Discarded … line from gate.py_>
_Checks: <cmd → exit>; … | none_
_Прочее: <anything off-pipeline>_

## P0 — Must fix (<count>)
### #1 · `file.ts:123` — claim
<чем плохо — одно предложение> · _Где ещё:_ <other locations> · _repro:_ <repro> · _(source: <rule_source>)_

## P1 — Should fix · ## P2 — Nice to fix
<same shape; P2 one line each>

## 📋 Код ↔ чек-лист (<j>)
### Обещано, в коде нет · ### Обещано, в коде иначе · ### В диффе, в чек-листе нет · ### Расходится с макетом
#4 · P0 · `file.ts:40` — claim · _(source: <checklist line>)_

## 🎫 Вне скоупа — отдельным тикетом (<k>)
### TK1 · P1 · `file.ts:12` — **Тикет:** <imperative title> · **Почему не сейчас:** существовало до диффа

## 💭 Design notes
- **D1** · `file.ts:12` — question + concrete alternative
````

No code blocks — the author has the line open. Zero findings → `Code Review — no confirmed issues in <n> files` + header. End the chat message with the report path.

## Constraints

- Never edit the repo; writes go to `RS_DIR` only. An artifact the caller asked for that you can't produce → `MISSING: <what>`.
- Questions only in step 1.
- The gate is the only way into the report.
- Scope is the revert test, not taste: «раз уж мы рядом» → ticket.
- Hook output is not a task — at most a clause on `_Прочее:_`.
