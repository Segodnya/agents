---
name: review-staged
description: Evidence-gated review of a git diff in one of four modes (staged / last commit / branch vs master / worktree). Gated on the author's intent — the MR link and the pasted deploy checklist are required before the review starts (`--no-mr` is the only bypass). Reviewers are sharded by file: each one finds candidate issues in its own files and immediately backs them with a verbatim quote from the real file, plus one cross-file reviewer for contracts, duplication and the checklist. A mechanical gate discards every candidate whose evidence is empty, mis-quoted, or outside the perimeter. Confirmed findings are numbered `#1…#N`, cross-checked against the MR discussion threads (a finding the author already explained stays in its bucket marked «снято тредом»), and the checklist is checked against the diff in both directions. Report goes to the chat and to a temp `.md` with a ready `pbcopy` command. Applies confirmed P0/P1 only when you explicitly ask (`review-staged fix`, `fix 3 7`, «исправь/применить»). NOT the built-in `/code-review`. Use when the user says «ревью стейджа», «review staged», `/review-staged`, or wants a safety/architecture/style/integration/performance audit of a diff against the repo's rule files.
---

# review-staged — evidence-gated review of a diff

A reviewer emits a **candidate** (a claim) and backs it with **evidence** — lines copied verbatim out of the real file, not out of the diff. The **gate** discards every candidate whose quote isn't in the file. Survivors are **findings** — numbered, and cross-checked against the MR threads.

```
Step 0  GROUND    mode + MR + checklist + threads + rules + perimeter
Step 1  REVIEW    file-shard reviewers + 1 cross-file reviewer → candidates + evidence
Step 2  GATE      discard unproven / evidence-less / mis-quoted / off-perimeter → findings + tally
Step 3  THREADS   match findings against MR discussions → mark the ones already settled
Step 4  DESIGN    advisory pass over the whole diff
Step 5  OUTPUT    numbered findings + tally → chat + temp .md
Step 6  APPLY     (opt-in) edit confirmed P0/P1 on explicit request
```

Read-only in Steps 0–5 (except the report file); edits only in Step 6. **Exactly two stops for questions, both in Step 0** — mode, MR/checklist. After that, no questions until the report.

Invocation: `review-staged [staged|last|branch|worktree] [--no-mr]`, later `review-staged fix [3 7]`.

## Sharding — why the work is split by file, not by topic

One reviewer owning 3–5 files reads those files **once** and carries all five charters over them; the diff and the rules are sent to it once. Splitting by topic instead would ship the whole diff to every reviewer and then re-open every file in a second verification pass — same findings, several times the tokens and the wall clock.

What that split can't see — a contract broken for callers in *another* file, the same logic duplicated across slices, the checklist as a whole — belongs to the single **cross-file reviewer**.

## The perimeter

**Perimeter = the diff hunks + the files that directly import them.** Reviewers read inside it, the gate discards anything cited outside it.

Navigate by **name**, from a cited line to the one thing it depends on (guard, caller, type def): `goToDefinition` / `findReferences` / `incomingCalls` / `hover` for ts/js/tsx, php, rust, go; `Grep` on a literal symbol or import specifier when LSP can't reach. Grep returns a position, never a survey.

**No repo-wide pattern sweeps** (`Grep` for `\.map\(.*\.find\(` across the tree, `find` over unrelated dirs) — they leave the perimeter and produce claims about untouched code. Who else does this? `findReferences` on the one symbol.

## Step 0 — Ground

### 0.1 Mode → file list

| Argument | Diff range |
| --- | --- |
| `staged`, `cached` | `git diff --staged` |
| `last` | `git diff HEAD~1..HEAD` |
| `branch` | `git diff $(git merge-base HEAD <base>)..HEAD` |
| `worktree` | `git diff HEAD` (staged + unstaged) |

`<base>` — from `rtk run git symbolic-ref --short refs/remotes/origin/HEAD` (strip `origin/`), fall back to `master`. No argument → `AskUserQuestion` with the four modes; pre-select `staged` when `rtk run git diff --cached --quiet` exits 1. Argument doesn't match the table → ask, don't guess.

`rtk run git diff <range> --name-only`. Empty → stop with "нет изменений в режиме `<mode>`", before asking anything else. Filter out binaries, lockfiles (`*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`), generated output (`*.min.*`, `dist/`, `build/`, `.next/`, `out/`), snapshots (`*.snap`), vendored dirs.

### 0.2 MR — required

1. MR URL in the invocation → use it.
2. Else `rtk run glab mr list --source-branch $(git branch --show-current)` → show what was found, confirm it's the right one.
3. Nothing found → `AskUserQuestion`: paste the URL / `--no-mr`.

No URL and no `--no-mr` → **stop**. `--no-mr` is the only bypass: it skips Step 3 and the cross-file reviewer's checklist charter, and the header says so.

### 0.3 Checklist — required

The deploy checklist (problem description, test cases, affected functionality) comes **pasted by the user** — never generated, never inferred from the diff.

Not in the invocation → ask for it and **wait**. Refused under `--no-mr` → continue with `Интент автора: не предоставлен`, no checklist charter. Refused with an MR → stop.

### 0.4 Threads

SKILL_DIR — the absolute path from the `Base directory for this skill:` line, not cwd:

```bash
python3 "SKILL_DIR/../audit-reply/scripts/fetch_mr.py" --url "<MR_URL>" --all
```

`--all` is mandatory: a resolved thread is exactly «уже обсудили». `audit-reply` not installed → `rtk run glab api "projects/:id/merge_requests/<iid>/discussions"`. Script fails (auth, wrong host) → print the `glab auth status --hostname <host>` hint, continue **without** thread matching, say so in the header.

### 0.5 Rules, perimeter, diff

1. **Rules** (in order, skip missing): root `CLAUDE.md` + nested ones it references; `AGENTS.md` + nested; `~/.claude/rules/*.md`; `docs/rules/*.md`. Drop any rule file whose frontmatter `paths` globs match none of the reviewable files (no `paths` key = always applies) — otherwise a PHP rulebook rides into a TypeScript review.
2. **Rules digest — built once, reused by every reviewer.** Copy the surviving rule text **verbatim**, dropping only sections whose scope matches no file in the diff (a `.php` section in a TS-only diff, a CSS section with no stylesheet touched). Never paraphrase, never summarize a rule into your own words — a reviewer citing a rule it can't quote is exactly the failure the gate exists for. Keep the source filename on every kept section, and pass the rule file **paths** alongside the digest so a reviewer can `Read` the original when it needs the full wording.
3. **Manifest:** which sources loaded / were missing / were skipped as not applicable / were trimmed out of the digest. Zero rules → universal mode (correctness / security / performance only), every `rule_source` tagged `"universal"`.
4. **Perimeter:** the filtered file list + each file's direct importers (one `Grep` per file on a literal import specifier, or `findReferences` on its exports). Write it down — Step 2 checks against it.
5. **Diff:** `rtk run git diff <range> -- <file>` per file, kept **per file** — Step 1 hands each reviewer only its own files' hunks. `rtk run` because the Bash hook rewrites bare `git diff` into a condensed diff that drops context and truncates.

## Step 1 — Review

### 1.1 Tiny diff → no agents

≤3 files **OR** <100 changed lines → run every charter **inline in the main context**, find and verify yourself, skip the spawns entirely. Steps 2–5 still run in full, gate included.

### 1.2 Shards

Group the filtered files into shards of **≤4 files** (keep files of one module together when it's free). Spawn **all shard reviewers plus the cross-file reviewer in a single message** (Read + Grep + LSP), `model: "sonnet"` for the shard reviewers — narrow slice, explicit charters, mechanical evidence work; the cross-file reviewer inherits the main model, it judges the change as a whole. Shards coming back consistently empty on a real diff → re-run that shard without the `model` override.

Each shard reviewer gets: its files' diff hunks, the rules digest + rule file paths, the checklist, the manifest, the perimeter, the prelude, and charters 1–4.

**Common prelude — prepend to every reviewer prompt:**

> - **Rules = the digest and the rule files it points at.** No rules from memory. Flag a violation only if it's traceable to a line you can quote from those, or is a universal correctness / security / performance principle. Name the rule file in `rule_source`.
> - **Diff-scope only.** Added/modified hunks in *your* files. No adjacent code, no pre-existing violations, no "while you're here". Exception: a diff-line depending on broken adjacent code the diff also touches.
> - **The checklist is the author's intent.** Behaviour it declares deliberate isn't a defect.
> - **Trust the linter.** Skip what eslint / stylelint / tsc covers.
> - **You find *and* prove.** State the claim, then open the real file and check the assumption under it ("`user` can be null here", "`n` is large", "no shared helper exists") — the guard two lines up, the caller, the type, the real size of the collection. Refuted by what you read → drop it yourself and count it in `dropped`. Can't settle it → drop it as `unproven`. Both are cheap and correct; a candidate backed by a quote you didn't copy is the one real failure.
> - **Evidence is mandatory on everything you return:**
>   - `evidence.quote` — the lines **copied verbatim** out of the file you just read, enough to make the problem visible (2–10 lines). Not paraphrased, not rebuilt from the diff, not re-indented. The gate greps this back into the file: a typed quote won't match and the candidate dies.
>   - `evidence.locations` — the `file:line` ranges you opened, including the corroborating one (`"no guard at userCard.tsx:40-58; caller passes raw props at list.tsx:12"`).
>   - `evidence.repro` — a command that would surface the problem when one exists (`yarn jest x.test.ts -t 'name'`, `tsc --noEmit`, `node -e "…"`, a curl, a URL + click path). **Report it, don't run it.** Omit for claims no command surfaces (naming, duplication, layering).
> - **`file` + `line` must name a real position inside the perimeter.** A candidate the gate can't locate dies there.
> - Confirmed P0/P1 → add `snippet_after`, the fixed version of the quoted lines. Optional for P2.
> - [paste "The perimeter" section verbatim]

Return shape — this and nothing else:

```json
{
  "candidates": [{
    "severity": "P0" | "P1" | "P2",
    "file": "path/to/file.ts",
    "line": 123,
    "claim": "one sentence: what is wrong and why it breaks",
    "rule_source": "architecture.md | code-style.md | checklist | universal | ...",
    "evidence": {
      "quote": "verbatim lines copied from the file",
      "locations": "userCard.tsx:40-58; list.tsx:12",
      "repro": "yarn jest src/userCard.test.tsx -t 'renders without user'"
    },
    "snippet_after": "fixed version of the quoted lines",
    "fix": "one-sentence rationale"
  }],
  "dropped": { "refuted": 0, "unproven": 0 }
}
```

- **P0** — bug, regression, security, data loss, type error, broken build, quadratic-or-worse on large n. Must fix.
- **P1** — architecture violation, integration risk, missing edge case, quadratic on bounded-but-growable input, rule violation that will cause pain. Should fix.
- **P2** — style, naming, duplication, micro-inefficiency, drift. Nice to fix.

### Charter 1 — Safety & Correctness

Correctness/security the rule files don't cover: logic bugs, null/undefined, off-by-one, races, unhandled rejections, async/await misuse, type errors, unsafe casts, XSS/injection, secrets in code, unsafe `dangerouslySetInnerHTML`, unvalidated input crossing trust boundaries, edge cases (empty arrays, long unbroken strings, overflow, missing fallbacks). A contract broken for callers **outside your files** is the cross-file reviewer's — inside them, name the callers and quote one (`findReferences` lists them).

### Charter 2 — Architecture & Integration

Structure and system-fit per the digest — cite the file in `rule_source`, don't invent rules:

- **Layer / slice / module boundaries** the diff contradicts.
- **Runtime + ship-time interactions:** i18n, cache/query keys, form-abandonment, lazy-loading, browser support, lifecycle ownership, error bubbling.
- **Deployment** (universal): feature flags, env vars, migration order, backward-incompatible payloads.

### Charter 3 — Style, Naming & Duplication

Style/naming the linter can't catch, per the digest. Plus magic numbers/strings that are repeated or semantically opaque. Duplication **within your files**; across slices it's the cross-file reviewer's.

### Charter 4 — Performance & Complexity

Algorithmic complexity hiding in clean-looking code, avoidable sequential I/O, and work the diff itself adds. Each P0/P1 claim names the delta (`O(n*m) → O(n+m)`, `+1 request → reuse existing query`).

- **Nested membership lookups** — `.find` / `.includes` / `.indexOf` / `.some` inside `.map` / `.filter` / `for` → lift into a `Set`/`Map` once.
- **Chained passes** — `.filter().map().find()` where one pass or early exit does.
- **`.sort()` in render or per-event** — sort once, memoize, or sort on write.
- **Heavy construction in a loop** — `new RegExp`, `JSON.parse(JSON.stringify(...))`, `new Date` from a constant → hoist.
- **Sequential `await` in a loop** with independent iterations → `Promise.all` / `allSettled`.
- **N+1** API/DB calls where a batched fetch exists.
- **Recursive tree/set building without memoization** on non-trivial input.
- **Quadratic string building** — `str += ...` over large n vs chunks + `join`.

Added runtime work — claim only where the react-hooks lint rule can't already see it:

- **Redundant request** — new `fetch`/query for data already in an existing query, cache, props, or parent.
- **Extra re-render** — new state/context, or an inline object/array/callback as a prop, re-rendering a subtree that lifting state, a ref, or memo avoids.
- **Over-firing effect** — `useEffect` dep recreated each render or too broad → repeat fetches/subscriptions.
- **Recompute each render** — non-trivial value rebuilt every render that `useMemo` / hoisting / deriving-once avoids.

### 1.3 Cross-file reviewer

One agent, the **whole** diff (all files' hunks), the digest, the checklist, the manifest, the perimeter, the same prelude. It owns only what a single shard can't see — no re-doing charters 1–4 file by file. It opens the files it cites and quotes them like everyone else.

- **Broken contracts across files** — changed signature / return type / payload shape, callers in other files not updated. Quote the changed signature *and* one un-updated caller.
- **Cross-slice duplication** — logic the diff adds where a shared module already exists; name and quote the helper (`workspaceSymbol` finds it).
- **i18n key lifecycle** — new keys missing from locale files, removed keys still referenced, hard-coded user-facing strings.
- **Breaking public-contract changes** callers in the diff don't update.
- **Checklist vs diff** (skipped when there's no checklist, `rule_source: "checklist"`) — read the checklist as a list of promises, check **both directions**:
  - **Promised, absent from the diff** — a case the checklist names («кейсы тестирования», «затронутый функционал») that no line implements. P0 when the checklist says it was fixed, P1 when it says it was touched.
  - **In the diff, not promised** — a behaviour change the checklist doesn't mention: the tester doesn't know to check it (P1). Refactors with no behaviour change don't count.
  - An absence has no quote and would die on the gate's quote check — so quote **the place where the promised thing should have been** (the sibling branch / handler / validation covering the paired case) and put the checklist's test case in `evidence.repro`. Read that place first — it may turn out handled after all. Can't name that place → don't emit the candidate.

## Step 2 — Gate

Mechanical, main-context, no judgment. Discard on the first failed check, tally the reason:

1. `evidence.quote` non-empty — else discard (*no evidence*).
2. **Quote is in the file:** `rtk run grep -nF '<longest distinctive line of the quote>' <file>`. No match → discard (*quote not in file*). Match far from `line` → correct `line` to the hit, keep.
3. `file` inside the Step 0 perimeter, and so is every file named in `evidence.locations` — else discard (*off-perimeter*).
4. `line` sits on a diff-touched line, or on a direct importer named as the corroborating location — else discard (*off-perimeter*).

Survivors are **findings**. Bookkeep them:

- **Dedup** by `(file, line, claim similarity)` — across shards too, the cross-file reviewer overlaps by design. Keep the higher severity, the more specific `rule_source`, the longer quote; merge `evidence.locations`.
- **Sort** P0 → P1 → P2, then by file, then by line.
- **Number** in that order: `#1 … #N`, sequential across all buckets. These numbers are how the user answers («3 и 7 валидны, 5 снимается») and how Step 6 selects.

Tally = the reviewers' own `dropped` counts (*refuted*, *unproven*) + everything discarded here. Carry it into the report. Zero findings → still report the tally and run Steps 4–5.

## Step 3 — Threads

Skipped under `--no-mr` or when Step 0.4 failed. Threads arrive **after** the gate: a reviewer that reads the author's explanations first stops emitting the candidate at all.

For each finding, look for a thread covering it — same `file` and `new_line` within ±10 lines, or the same claim in prose (a thread may sit on a line that has since moved). Then judge the explanation — don't defer to it, «так задумано» without a reason closes nothing:

- **Closes the finding** — it **stays in its bucket**, keeps its number, gets `✅ снято тредом #<n>` with the author's quote and one line of why it's accepted. Subtracted from the headline count, **never applied** in Step 6.
- **Doesn't close it** — stays as a normal finding, plus one line: what the author answered and why it doesn't cover the case.

No matching thread → the finding is untouched.

## Step 4 — Design judgment (advisory)

The gate only passes quotable defects. Do **one** holistic pass over the diff on what it can't judge — is this change the right shape at all:

1. **Needed at all?** — real problem solved, or dead/speculative code, a config that defaults the same way, a guard for a case that can't happen?
2. **Adds work that wasn't there?** — a request / render / effect / subscription the app didn't do before and doesn't need. (Quotable instances belong to charter 4; here it's the *pattern*.)
3. **Categorically simpler path?** — not "this helper already exists" (charter 3 owns that), but a different approach: derived state instead of synced state, a built-in instead of a hand-rolled loop, deleting code instead of adding a branch.
4. **Touched modules want deepening?** — changed modules and their immediate **seams** only (a seam = where an interface meets its callers; not a whole-repo walk). Is the diff adding a **shallow** module (interface nearly as complex as its implementation), splitting one concept across many tiny modules, or leaking state across a seam? **Deletion test:** delete what the diff adds — does complexity *concentrate* in one place (it earns its keep) or *move* across callers (it's a pass-through)? Name the deeper shape and its payoff in **locality** (change, bugs and knowledge land in one place) and **leverage** (callers get more behind a smaller interface).

Rules:

- **Advisory, never a finding** — own section, numbered `D1…D3`, never in the P0/P1/P2 buckets.
- **≤3 necessity notes (1–3) + ≤3 deepening notes (4). Silence is valid.**
- **Each note = one question + one concrete alternative.** Can't name the simpler path (or the deeper shape)? You don't have a note.
- **Read-only and one-shot.** No grilling loop, no "which would you like to explore?", no `CONTEXT.md` / ADRs. Point the author to `/improve-codebase-architecture` for a deepening note worth pursuing.

## Step 5 — Output

The quote is the "before" block; `locations` and `repro` go on the `_Проверено:_` line.

**The full report goes to the file. The chat gets the actionable half** — same text, same numbers, verbatim from the report, minus the P2 bodies:

| | file | chat |
| --- | --- | --- |
| header + discard line | ✅ | ✅ |
| P0, P1 (full, with quotes) | ✅ | ✅ |
| P2 | full list | one line: `## P2 — Nice to fix (<count>) — в отчёте` |
| Summary, design notes | ✅ | ✅ |

````markdown
# Code Review — <N> находок (<M> снято тредами) · режим: <mode> · MR !<iid>
_Applied rules: <list>_ · _Missing: <list or "none">_
_Чек-лист: принят_ · _Треды: <T> (открытых <O>, резолвленных <R>)_ · _Спека: проверена_
_Discarded <D> of <T> candidates: <a> unproven, <b> refuted, <c> no evidence, <d> quote not in file, <e> off-perimeter._

## P0 — Must fix (<count>)

### #1 · `file.ts:123` — claim

```ts
// in file
<evidence.quote>
```
```ts
// after
<snippet_after>
```

_Why:_ <fix> _(source: <rule_source>)_.
_Проверено:_ <evidence.locations> · _repro:_ `<evidence.repro>`.

### #2 · `file.ts:40` — claim ✅ снято тредом #7

> @<author>, тред #7: «<цитата>»

_Оценка:_ <одна строка: почему объяснение закрывает находку>. В `fix` не идёт.

## P1 — Should fix (<count>)
Same shape as P0.

## P2 — Nice to fix (<count>)
- **#8 · `file.ts:78`** — claim. _Fix:_ <one-liner> _(source: <rule_source>)_. _Проверено:_ <evidence.locations>.

## Summary
<2-3 sentences: risk level, headline concerns, merge recommendation.>

## 💭 Design notes (advisory, ungated)
> Not bugs — questions about the change's intent and shape (Step 4); you may be missing the author's context. Omit the section when there are none.
- **D1 · Needed?** one sentence + concrete alternative
- **D2 · Added work:** what it adds and why it isn't needed
- **D3 · Simpler path / Deepen?** a categorically simpler approach, or a shallow/leaky module the diff touches + the deeper shape in locality/leverage terms; end with «to explore: /improve-codebase-architecture» when worth pursuing
````

- Header flags reflect reality: `_Интент автора: не предоставлен_` under `--no-mr`, `_Спека: не проверялась_` without a checklist, `_Треды: недоступны (<причина>)_` when Step 0.4 failed.
- The discard line is always present; drop zero-count reasons, write `_Discarded 0 of <T> candidates._` when nothing was gated out.
- Right language hint in fences (`ts`, `tsx`, `css`, `twig`, …). Omit empty buckets.
- Zero findings → `Code Review — no confirmed issues in <N> files / <M> lines.` + header + discard line, in both places.

**Save the report.** The full text, verbatim, via `Write` to `/tmp/review-<repo>-<MR-iid|mode>-<YYYYMMDD-HHmm>.md`. Last two lines of the chat message:

```
Отчёт: /tmp/review-core_backend-29876-20260801-1420.md
pbcopy < /tmp/review-core_backend-29876-20260801-1420.md
```

≥1 unresolved P0/P1 → also end with `Применить подтверждённые P0/P1? (review-staged fix, или fix 3 7)`. Wait for the user.

## Step 6 — Apply (opt-in)

Triggered **only** by an explicit request after the report: `review-staged fix`, `fix 3 7`, «исправь», «применить», «накати фиксы». Never in the same turn as the report.

1. **Selection:** numbers given → exactly those findings (any severity). No numbers → all confirmed P0/P1, skipping P2.
2. **Never** findings marked `✅ снято тредом` — unless the user names their number explicitly.
3. `Edit` each file with `evidence.quote` → `snippet_after`. No longer matches (the file drifted)? Skip and report as stale, don't guess.
4. **Surgical** — exactly what the finding describes. No adjacent cleanups, no reformatting, no renames «заодно».
5. List what changed (`#N` → `file:line`) and what was skipped (stale / P2 / снято тредом); append the same list to the report file as `## Применено`.
6. Do **NOT** `git add` / `git commit`, do **NOT** run `build`. Leave staging to the user.

## Hard constraints

- **Two stops, both in Step 0** (mode, MR/checklist). No plan mode, no `ExitPlanMode`, no other slash commands, no questions between Step 1 and the report.
- **The gate is the only door.** Every reported finding passed all four Step 2 checks with a quote that greps clean — including in the tiny-diff branch. Re-reading the diff is not verification; the quote comes out of the real file.
- **The digest never replaces the rule text.** It's the same lines, minus the inapplicable sections. A `rule_source` you can't point at a real line in a real file is `"universal"` or it's nothing.
- **Hook output is not a task.** Anything a `Stop` / `PostToolUse` hook prints (eslint, stylelint, tsc, prettier) arrives outside the pipeline and never passed the gate. Don't fix code because of it, don't add it to the buckets, don't start another round. At most one line in the Summary: «хук сообщил: N ошибок линтера в `<file>`». Edits only on explicit request.
- **Design notes (Step 4) are the only ungated text** in the report; never in the P0/P1/P2 buckets.
- Cite rules by filename via `rule_source`; never restate rule files in the report.
- Invalid JSON from a reviewer → note it in the Summary, count its candidates in the tally as *unparseable*, continue with the rest.
