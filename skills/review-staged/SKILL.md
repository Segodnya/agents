---
name: review-staged
description: Review of a git diff in one of four modes (staged / last commit / branch vs master / worktree) by four parallel reviewers — correctness & contracts, rules & smells (+ design notes), performance & complexity, and the deploy checklist. Every finding carries a verbatim quote from the real file; unquotable claims are dropped. Findings already settled in the MR discussion threads are marked as such; real defects that pre-date the diff become ticket drafts instead of findings. Report goes to the chat and to a temp `.md`; the skill never edits code. Flags: `--no-mr` (no MR link), `--no-spec` (no deploy checklist). NOT the built-in `/code-review`. Use when the user says «ревью стейджа», «review staged», `/review-staged`, or wants a safety/architecture/style/integration/performance audit of a diff.
---

# review-staged

```
1 GROUND     mode → diff · rules · MR · checklist
2 REVIEW     4 agents in parallel → candidates + evidence
3 GATE       quote check → findings #1…#N + tickets T1…Tn
4 THREADS    mark findings + design notes the MR discussion already settled
5 REPORT     chat + temp .md
```

Invocation: `review-staged [staged|last|branch|worktree] [--no-mr] [--no-spec]`.

## 1. Ground

| Argument | Range |
| --- | --- |
| `staged`, `cached` | `git diff --staged` |
| `last` | `git diff HEAD~1..HEAD` |
| `branch` | `git diff $(git merge-base HEAD <base>)..HEAD` |
| `worktree` | `git diff HEAD` |

`<base>` from `git symbolic-ref --short refs/remotes/origin/HEAD` minus `origin/`, else `master`. No argument → `AskUserQuestion` with the four modes.

`RS_DIR` = `/tmp/review-staged-<repo>-<branch>`. Substitute that **literal** path into every command — shell state doesn't survive between `Bash` calls, `$VAR` won't work.

```bash
mkdir -p /tmp/review-staged-<repo>-<branch>
git diff <range> --name-only          # empty → stop: "нет изменений в режиме <mode>"
git diff <range> > /tmp/review-staged-<repo>-<branch>/diff   # bare git, never `rtk run git`
```

Drop from the file list: binaries, `*.lock` / `*-lock.json`, `dist/` `build/` `.next/` `out/`, `*.min.*`, `*.snap`, vendored dirs.

**Rules** — the repo's path-scoped rules (`paths:` frontmatter in `.claude/rules` / `.agents/rules` / `.cursor/rules`) are collected **here, by you**, never by a reviewer. Auto-injection is not a channel: it fires only on a native `Read` of a matching file — not on `cat`/`sed` — and never inside a subagent. `SKILL_DIR` = the absolute path from the harness's `Base directory for this skill:` line, not cwd:

```bash
git diff <range> --name-only \
  | python3 "SKILL_DIR/scripts/match_rules.py" <repo-root> \
  > /tmp/review-staged-<repo>-<branch>/rules.md
```

stderr prints `matched <k> of <n>` and both lists — that line goes into the report header verbatim. `matched 0` or no rules dir → the file stays empty and the header says `_Applied rules: нет path-scoped правил_`; that is a valid outcome, inventing one is not.

**MR** — URL in the invocation, else `glab mr list --source-branch $(git branch --show-current)` and confirm. Neither, and no `--no-mr` → stop and ask. Fetch threads **to a file, don't read them** (step 4 reads them):

```bash
python3 "SKILL_DIR/../audit-reply/scripts/fetch_mr.py" --url "<MR_URL>" --all \
  > /tmp/review-staged-<repo>-<branch>/threads.json
```

`--all` is mandatory — resolved threads are exactly «уже обсудили». No `audit-reply` → `glab api "projects/:id/merge_requests/<iid>/discussions"` into the same file. Fails → skip step 4, say so in the header.

**Checklist** — pasted by the user, never generated from the diff. Missing and no `--no-spec` → ask and wait.

## 2. Reviewers

Spawn all four **in one message** (three under `--no-spec` — D is skipped), `model: "sonnet"` on each. Each gets: the literal path to `RS_DIR/diff`, the file list, the checklist text inline. Never the threads. `RS_DIR/rules.md` goes to **B only** — no other charter reads it.

≤3 files **or** <300 changed lines → run the same charters inline yourself. The inline branch skips the *spawns*, nothing else: steps 1 and 3–5 unchanged, `cat RS_DIR/rules.md` before the B axis exactly as a reviewer would.

**Prelude — prepend to every reviewer:**

> - Read `RS_DIR/diff` **once**, at the start of your work. After that go by what is already in your context — re-reading the same file adds nothing and costs the whole diff again. Need one fragment back → `Read` with `offset`/`limit`, or `sed -n 'A,Bp'`, never the file whole.
> - Hunt inside the diff. Read adjacent code only to check a claim about a diff line.
> - State the claim, then open the real file and check the assumption under it (guard above, caller, type, actual collection size). Refuted → `dropped.refuted`. Can't settle → `dropped.unproven`.
> - `evidence.quote` mandatory — 2–10 lines copied verbatim out of the file, not retyped. The gate greps it back.
> - `evidence.locations` — every `file:line` you opened. `evidence.repro` — a command or click path (`tsc --noEmit`, `jest -t '…'`, URL). **Report it, don't run it.** Omit where none applies.
> - `pre_existing` — revert test: would this defect still be here if the diff's lines were removed? True → ticket, not dropped. Cap 3 per reviewer, P0/P1 only.
> - The checklist is the author's intent — behaviour it declares deliberate isn't a defect.
> - Skip what eslint / stylelint / tsc catch.
> - Name the defect, not the patch — no code to apply, no «сделай так».
> - Navigate by name: LSP (`goToDefinition` / `findReferences` / `incomingCalls` / `hover`) for ts/js/tsx, php, rust, go — `ToolSearch("select:LSP")` first; else `grep`/`rg` via Bash (no `Grep`/`Glob` tool in this session; quote globs for zsh). No repo-wide pattern sweeps.

```json
{
  "candidates": [{
    "severity": "P0" | "P1" | "P2",
    "file": "path/to/file.ts", "line": 123,
    "claim": "one sentence: what is wrong and why it breaks",
    "rule_source": "CLAUDE.md | smell:Feature Envy | checklist | universal",
    "pre_existing": false,
    "evidence": { "quote": "…", "locations": "a.tsx:40-58; b.tsx:12", "repro": "…" }
  }],
  "design_notes": ["only reviewer B fills this"],
  "dropped": { "refuted": 0, "unproven": 0 }
}
```

**P0** — bug, regression, security, data loss, type error, broken build, quadratic on large n. **P1** — architecture violation, integration risk, missing edge case, rule violation that will hurt. **P2** — style, naming, duplication, drift.

### A · Correctness & contracts

- Logic: null/undefined, off-by-one, races, unhandled rejections, async/await misuse, type errors, unsafe casts.
- Security: XSS/injection, secrets, `dangerouslySetInnerHTML`, unvalidated input crossing a trust boundary.
- Edge cases: empty collections, long unbroken strings, overflow, missing fallbacks.
- **Broken contract** — signature / return type / payload changed, callers not updated. Quote the new signature **and** one stale caller.
- **i18n** — new keys missing from locales, removed keys still referenced, hard-coded user-facing strings.

### B · Rules & smells (+ design notes)

**Rules** — the repo's path-scoped rules, already collected for you:

```bash
cat /tmp/review-staged-<repo>-<branch>/rules.md   # literal path comes in the prompt
```

Read it **in full** before the first candidate — pre-filtered to the changed files, `===== RULE FILE: <path> =====` separates them. Empty or absent → don't go looking. On top of it: `~/.claude/CLAUDE.md` + `~/.claude/rules/*.md`, the project's `CLAUDE.md` / `AGENTS.md`. Nothing from memory — an unread rule is not a rule. Cite as `rule_source: "<rule file>: «<the rule line, verbatim>»"`, path as printed in the separator; the gate greps that line back.

**Smells** — Fowler's catalogue: Mysterious Name, Duplicated Code, Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man, Refused Bequest. `P2` unless it bites; a documented rule overrides it; skip what tooling enforces. Name the smell, quote the hunk.

**Design notes** (`design_notes`, advisory, ≤3, silence is valid) — one question each on a named hunk; no hunk → no note; pre-dates the diff → ticket.

1. **Needed at all?** — dead/speculative code, a config that defaults the same way, a guard for an impossible case.
2. **Adds work that wasn't there?** — a request / render / effect / subscription the app didn't do before.
3. **Simpler path?** — synced state that could be derived, a hand-rolled loop the platform covers.

### C · Performance & complexity

Every P0/P1 names the delta (`O(n*m) → O(n+m)`, `+1 request → reuse existing query`). Only what the react-hooks lint rule can't see.

- Nested membership — `.find`/`.includes`/`.some` inside `.map`/`.filter`/`for` → `Set`/`Map` once. Chained passes where one pass or an early exit does; `.sort()` in render or per-event.
- Heavy construction in a loop (`new RegExp`, `JSON.parse(JSON.stringify(…))`, `new Date` from a constant); recursion without memoization; quadratic `str +=`.
- Sequential `await` over independent iterations → `Promise.all`. N+1 where a batched fetch exists. **Redundant request** — data already in a query, cache, props or parent.
- **Extra re-render** — new state/context, inline object/callback prop. **Over-firing effect** — dep recreated each render or too broad. **Recompute each render** — non-trivial value rebuilt every time.

### D · Spec — checklist vs diff

Skipped under `--no-spec`. `rule_source: "checklist"`. Check **both directions**:

- **Promised, absent from the diff** — P0 if the checklist says it was fixed, P1 if it says it was touched.
- **In the diff, not promised** — a behaviour change the tester doesn't know to check (P1). Pure refactors don't count.

An absence has no quote: quote **the place where it should have been** (sibling branch, handler, validation of the paired case) — read it first, it may turn out handled — and put the checklist's test case in `repro`. Can't name the place → no candidate.

## 3. Gate

Mechanical, no judgment. Discard on the first failure and tally the reason:

1. `evidence.quote` non-empty — else *no evidence*.
2. `grep -nF '<longest distinctive line of the quote>' <file>` — **bare `grep`, never `rtk run grep`** (`sh -c` re-parse kills a quote with `(`, `'`, `"`). No match → *quote not in file*. Match far from `line` → fix `line`, keep.
3. `rule_source` names a `.md` file → `grep -nF '<its «…» line>' <that file>`. No match → *rule not in file*. `smell:` / `checklist` / `universal` skip this check.
4. `file` and every file in `locations` is in the diff or directly imports a diff file — else *off-perimeter*. Exception: a `rule_source: "checklist"` absence may point at the paired place, wherever it lives.

Route by the revert test: `pre_existing: true` → ticket `T`; else finding `#`.

Dedup by `(file, line, claim)` across reviewers — keep the higher severity, the longer quote, merge `locations`; a claim in both buckets is a finding. Sort P0 → P1 → P2, then file, then line. Number `#1…#N` and `T1…Tn` as two independent sequences.

Tally = the reviewers' own `dropped` + everything discarded here. Tickets are routed, not discarded.

## 4. Threads

Skipped under `--no-mr`. Read `RS_DIR/threads.json` **here, for the first time**. Findings **and design notes** go through this step — tickets never do.

Read it in two passes, with the script — never `cat`, never a `jq`/`python3` one-liner of your own (a 76 KB dump is normal: a root note carries the previous round's pasted report):

```bash
python3 "SKILL_DIR/../audit-reply/scripts/read_threads.py" /tmp/review-staged-<repo>-<branch>/threads.json          # index
python3 "SKILL_DIR/../audit-reply/scripts/read_threads.py" /tmp/review-staged-<repo>-<branch>/threads.json 3 7 12   # full text
```

The index is for **selecting** a thread, never for judging it. Pull the full text of every thread whose file matches a finding's or a note's file, plus every general thread — in one call, all numbers at once. Bodies come out whole; **no slicing, no `[:600]`** — the reviewer dictates the shape of the fix in the tail of the thread.

Match by `file` + `new_line` ±10, or by the same claim in prose. Judge the explanation — «так задумано» without a reason closes nothing:

- **Closes it** → keeps its number, gets `✅ снято тредом #<n>` with the author's quote and one line of why it's accepted; subtracted from the headline count.
- **Doesn't** → stays a finding + one line on why the answer doesn't cover the case.

Design notes have a lower bar: any thread that argued the same claim either way **kills the note** — it goes to `_Прочее:_` as «D<n> снят тредом #<n>», never re-asked with `✅`. A note no thread touched stays. Last round's report is itself a thread root, so a note repeating what you asked last round is settled, not new.

A re-reviewed MR carries last round's report as a thread root (`# Code Review — <N> находок`). No reply under it → keep the finding, mark «повтор #<n>, без ответа автора». Numbering doesn't carry over between rounds.

## 5. Report

Full text to the file; chat gets the same minus P2 and ticket bodies (each collapses to `## P2 — Nice to fix (<count>) — в отчёте`).

````markdown
# Code Review — <N> находок (<M> снято тредами) · вне скоупа: <k> · режим: <mode> · MR !<iid>

_Треды: <Th> (открытых <O>)_ · _Чек-лист: принят_ · _Applied rules: <the `matched <k> of <n>` line from step 1>_
_Discarded <D> of <C> candidates: <a> unproven, <b> refuted, <c> no evidence, <d> quote not in file, <e> rule not in file, <f> off-perimeter, <g> unparseable._
_Прочее: <всё, что пришло мимо пайплайна — хук, невалидный JSON от ревьюера>_

## P0 — Must fix (<count>)

### #1 · `file.ts:123` — claim

```ts
<evidence.quote>
```

_Проверено:_ <locations> · _repro:_ `<repro>` · _(source: <rule_source>)_.

### #2 · `file.ts:40` — claim ✅ снято тредом #7

> @<author>: «<цитата>»
_Оценка:_ <почему закрывает>.

## P1 — Should fix (<count>)

Same shape.

## P2 — Nice to fix (<count>)

- **#8 · `file.ts:78`** — claim _(source: <rule_source>)_.

## 🎫 Вне скоупа — отдельным тикетом (<k>)
> Существовало до этого диффа. В этой задаче не чиним.

### T1 · P1 · `list.tsx:12`

**Тикет:** <заголовок в повелительном наклонении>
**Что:** <claim> · **Где:** <locations>
**Почему не сейчас:** существовало до диффа; дифф трогает <что рядом>

```ts
<evidence.quote>
```

## 💭 Design notes (advisory, no quote gate — но через треды)
- **D1 · Needed?** question + concrete alternative
````

- `_Чек-лист: не предоставлен_` under `--no-spec`; `_Треды: недоступны (<причина>)_` when the fetch failed; drop `· MR !<iid>` under `--no-mr`.
- Drop zero-count discard reasons, the `🎫` section when `k = 0`, and empty buckets. Right language hint in fences.
- Zero findings → `Code Review — no confirmed issues in <files> files / <lines> lines.` + header + discard line; the 🎫 section still goes to the file.

Save the full text verbatim via `Write` to `/tmp/review-<repo>-<iid|mode>-<YYYYMMDD-HHmm>.md`, and end the chat message with:

```
Отчёт: /tmp/review-repo-29876-20260825-1420.md
pbcopy < /tmp/review-repo-29876-20260825-1420.md
```

## Hard constraints

- Never write code, never touch the repo — the report in `/tmp` is the only write. Asked to fix → separate task.
- Questions only in step 1. No sharding — one reviewer per axis, all files.
- Every `Agent` spawn carries `model: "sonnet"`.
- After the spawn message emit nothing until a report lands — no `echo`/`sleep`/`date`, no status narration, no "meanwhile" reading.
- Threads and previous reports never go into a reviewer prompt.
- The gate is the only door, inline branch included — the quote comes out of the real file, not the diff.
- Path-scoped rules are collected in step 1 and consumed from `RS_DIR/rules.md`. A rules dir that exists and was never read is a broken run, not a lighter one.
- Scope is the revert test, not taste. «Раз уж мы рядом» → ticket.
- Hook output is not a task — at most a clause on `_Прочее:_`.
- Design notes skip the quote gate, but never the thread check (step 4).
- Invalid JSON from a reviewer → note it on `_Прочее:_`, count its candidates as *unparseable*, continue.
