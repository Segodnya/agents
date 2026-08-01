---
name: review-staged
description: Evidence-gated review of a git diff in one of four modes (staged / last commit / branch vs master / worktree). Gated on the author's intent — the MR link and the pasted deploy checklist are required before the review starts (`--no-mr` is the only bypass). Finders emit candidate issues, a verifier opens the real file and attaches a verbatim quote as evidence, and a mechanical gate discards every candidate whose evidence is empty, mis-quoted, or outside the perimeter. Confirmed findings are numbered `#1…#N`, cross-checked against the MR discussion threads (a finding the author already explained stays in its bucket marked «снято тредом»), and a spec finder checks the diff against the checklist in both directions. Report goes to the chat and to a temp `.md` with a ready `pbcopy` command. Applies confirmed P0/P1 only when you explicitly ask (`review-staged fix`, `fix 3 7`, «исправь/применить»). NOT the built-in `/code-review`. Use when the user says «ревью стейджа», «review staged», `/review-staged`, or wants a safety/architecture/style/integration/performance audit of a diff against the repo's rule files.
---

# review-staged — evidence-gated review of a diff

A finder emits a **candidate** (a claim). A verifier opens the real file and attaches **evidence** (a verbatim quote). The **gate** discards every candidate whose quote isn't in the file. Survivors are **findings** — numbered, and cross-checked against the MR threads.

```
Step 0  GROUND    mode + MR + checklist + threads + rules + perimeter
Step 1  FIND      5 finders → candidates {severity, file, line, claim}
Step 2  VERIFY    1 verifier per file → evidence (verbatim quote + repro command)
Step 3  GATE      discard unproven / evidence-less / mis-quoted / off-perimeter → findings + tally
Step 4  THREADS   match findings against MR discussions → mark the ones already settled
Step 5  DESIGN    advisory pass over the whole diff
Step 6  OUTPUT    numbered findings + tally → chat + temp .md
Step 7  APPLY     (opt-in) edit confirmed P0/P1 on explicit request
```

Read-only in Steps 0–6 (except the report file); edits only in Step 7. **Exactly two stops for questions, both in Step 0** — mode, MR/checklist. After that, no questions until the report.

Invocation: `review-staged [staged|last|branch|worktree] [--no-mr]`, later `review-staged fix [3 7]`.

## The perimeter

**Perimeter = the diff hunks + the files that directly import them.** Finders read inside it, verifiers read inside it, the gate discards anything cited outside it.

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

No URL and no `--no-mr` → **stop**. `--no-mr` is the only bypass: it skips Step 4 and Finder 5, and the header says so.

### 0.3 Checklist — required

The deploy checklist (problem description, test cases, affected functionality) comes **pasted by the user** — never generated, never inferred from the diff.

Not in the invocation → ask for it and **wait**. Refused under `--no-mr` → continue with `Интент автора: не предоставлен`, no Finder 5. Refused with an MR → stop.

### 0.4 Threads

SKILL_DIR — the absolute path from the `Base directory for this skill:` line, not cwd:

```bash
python3 "SKILL_DIR/../audit-reply/scripts/fetch_mr.py" --url "<MR_URL>" --all
```

`--all` is mandatory: a resolved thread is exactly «уже обсудили». `audit-reply` not installed → `rtk run glab api "projects/:id/merge_requests/<iid>/discussions"`. Script fails (auth, wrong host) → print the `glab auth status --hostname <host>` hint, continue **without** thread matching, say so in the header.

### 0.5 Rules, perimeter, diff

1. **Rules** (in order, skip missing): root `CLAUDE.md` + nested ones it references; `AGENTS.md` + nested; `~/.claude/rules/*.md`; `docs/rules/*.md`. Drop any rule file whose frontmatter `paths` globs match none of the reviewable files (no `paths` key = always applies) — otherwise a PHP rulebook rides into a TypeScript review.
2. **Manifest:** which sources loaded / were missing / were skipped as not applicable. Zero rules → universal mode (correctness / security / performance only), every `rule_source` tagged `"universal"`.
3. **Perimeter:** the filtered file list + each file's direct importers (one `Grep` per file on a literal import specifier, or `findReferences` on its exports). Write it down — Step 3 checks against it.
4. **Diff:** `rtk run git diff <range> -- <file>` per file, concatenated. `rtk run` because the Bash hook rewrites bare `git diff` into a condensed diff that drops context and truncates; per-file also avoids oversized blobs.

## Step 1 — Find

Spawn **5 finders in a single message** (Read + Grep + LSP), each with the full diff, the rule files inline, the checklist, the manifest, the perimeter, and its charter. Each returns a JSON array of candidates, nothing else.

**Tiny-diff branch:** ≤3 files OR <100 changed lines → run all five charters yourself inline. Steps 2–4 still run in full.

**Common prelude — prepend to every finder prompt:**

> - **Rules = the loaded files only.** No rules from memory. Flag a violation only if it's traceable to a line in the loaded rules, or is a universal correctness / security / performance principle. Name the rule file in `rule_source`.
> - **Diff-scope only.** Added/modified hunks. No adjacent code, no pre-existing violations, no "while you're here". Exception: a diff-line depending on broken adjacent code the diff also touches.
> - **The checklist is the author's intent.** Behaviour it declares deliberate isn't a defect. (Whether the diff *matches* the checklist is Finder 5's job, not yours.)
> - **Trust the linter.** Skip what eslint / stylelint / tsc covers.
> - **You emit candidates, not verdicts.** State the claim and the assumption under it ("`user` can be null here", "`n` is large", "no shared helper exists"); a verifier will back it with a quote or kill it. Spend your reads on *aiming* (a quick `hover` / `goToDefinition` that you're on the right line and symbol), not on proving. An assumption you already know is refuted — drop it yourself.
> - **`file` + `line` must name a real position inside the perimeter.** A candidate the verifier can't locate dies in the gate.
> - [paste "The perimeter" section verbatim]

```json
{
  "severity": "P0" | "P1" | "P2",
  "file": "path/to/file.ts",
  "line": 123,
  "claim": "one sentence: what is wrong and why it breaks",
  "rule_source": "architecture.md | code-style.md | checklist | universal | ..."
}
```

- **P0** — bug, regression, security, data loss, type error, broken build, quadratic-or-worse on large n. Must fix.
- **P1** — architecture violation, integration risk, missing edge case, quadratic on bounded-but-growable input, rule violation that will cause pain. Should fix.
- **P2** — style, naming, duplication, micro-inefficiency, drift. Nice to fix.

### Finder 1 — Safety & Correctness

Correctness/security the rule files don't cover: logic bugs, null/undefined, off-by-one, races, unhandled rejections, async/await misuse, type errors, unsafe casts, XSS/injection, secrets in code, unsafe `dangerouslySetInnerHTML`, unvalidated input crossing trust boundaries, broken contracts (changed return type, callers not updated), edge cases (empty arrays, long unbroken strings, overflow, missing fallbacks). For a broken-contract claim, name the callers you expect to break (`findReferences` lists them).

### Finder 2 — Architecture & Integration

Structure and system-fit per the loaded rules — cite the file in `rule_source`, don't invent rules:

- **Layer / slice / module boundaries** the diff contradicts.
- **Runtime + ship-time interactions:** i18n, cache/query keys, form-abandonment, lazy-loading, browser support, lifecycle ownership, error bubbling.
- **i18n key lifecycle** (universal): new keys missing from locale files, removed keys still referenced, hard-coded user-facing strings.
- **Deployment** (universal): feature flags, env vars, migration order, backward-incompatible payloads, breaking public-contract changes callers in the diff don't update.

### Finder 3 — Style, Naming & Duplication

Style/naming the linter can't catch, per the loaded rules. Plus **cross-slice duplication** — logic repeated where a shared module belongs; name the helper you believe exists (`workspaceSymbol` finds it) so the verifier can quote it. Plus magic numbers/strings that are repeated or semantically opaque.

### Finder 4 — Performance & Complexity

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

### Finder 5 — Checklist vs diff

Skipped when there's no checklist. `rule_source: "checklist"`. Reads the checklist as a list of promises, checks **both directions**:

- **Promised, absent from the diff** — a case the checklist names («кейсы тестирования», «затронутый функционал») that no line implements. P0 when the checklist says it was fixed, P1 when it says it was touched.
- **In the diff, not promised** — a behaviour change the checklist doesn't mention: the tester doesn't know to check it (P1). Refactors with no behaviour change don't count.

An absence has no quote and would die on the gate's quote check — so name **the place where the promised thing should have been** (the sibling branch / handler / validation covering the paired case), and put the checklist's test case in `evidence.repro`. Can't name that place → don't emit the candidate.

## Step 2 — Verify

Group candidates by `file`, spawn **one verifier per file, all in a single message** (Read + Grep + LSP). Each gets that file's candidates, that file's diff, the perimeter, and this charter:

> `Read` the cited file around the cited line, check the assumption the claim rests on (the guard two lines up, the caller, the type, the real size of the collection), return each candidate with a `verdict` — and, when confirmed, `evidence`.
>
> - `evidence.quote` — the lines **copied verbatim** out of the file you just read, enough to make the problem visible (2–10 lines). Not paraphrased, not rebuilt from the diff, not re-indented. The gate greps this back into the file: a typed quote won't match and the candidate dies.
> - `evidence.locations` — the `file:line` ranges you opened, including the corroborating one (`"no guard at userCard.tsx:40-58; caller passes raw props at list.tsx:12"`).
> - `evidence.repro` — a command that would surface the problem when one exists (`yarn jest x.test.ts -t 'name'`, `tsc --noEmit`, `node -e "…"`, a curl, a URL + click path). **Report it, don't run it.** Omit for claims no command surfaces (naming, duplication, layering).
> - **`rule_source: "checklist"` candidates** claim something is *missing*: quote the place where it should have been (the paired branch/handler the candidate names), `repro` = the checklist's test case. Read that place — it may turn out handled after all, then `refuted`.
> - `verdict: "refuted"` when the file shows it can't happen — name the line that refutes it. `"unproven"` when you can't settle it. Both are cheap and correct; a `confirmed` backed by a quote you didn't copy is the one real failure.
> - Confirmed P0/P1 → add `snippet_after`, the fixed version of the quoted lines. Optional for P2.
> - [paste "The perimeter" section verbatim]

```json
{
  "severity": "P0", "file": "path/to/file.ts", "line": 123,
  "claim": "...", "rule_source": "...",
  "verdict": "confirmed" | "refuted" | "unproven",
  "evidence": {
    "quote": "verbatim lines copied from the file",
    "locations": "userCard.tsx:40-58; list.tsx:12",
    "repro": "yarn jest src/userCard.test.tsx -t 'renders without user'"
  },
  "snippet_after": "fixed version of the quoted lines",
  "fix": "one-sentence rationale"
}
```

## Step 3 — Gate

Mechanical, main-context, no judgment. Discard on the first failed check, tally the reason:

1. `verdict === "confirmed"` — else discard (*unproven*).
2. `evidence.quote` non-empty — else discard (*no evidence*).
3. **Quote is in the file:** `rtk run grep -nF '<longest distinctive line of the quote>' <file>`. No match → discard (*quote not in file*). Match far from `line` → correct `line` to the hit, keep.
4. `file` inside the Step 0 perimeter, and so is every file named in `evidence.locations` — else discard (*off-perimeter*).
5. `line` sits on a diff-touched line, or on a direct importer named as the corroborating location — else discard (*off-perimeter*).

Survivors are **findings**. Bookkeep them:

- **Dedup** by `(file, line, claim similarity)` — keep the higher severity, the more specific `rule_source`, the longer quote; merge `evidence.locations`.
- **Sort** P0 → P1 → P2, then by file, then by line.
- **Number** in that order: `#1 … #N`, sequential across all buckets. These numbers are how the user answers («3 и 7 валидны, 5 снимается») and how Step 7 selects.

Carry the tally (count + reason breakdown) into the report. Zero findings → still report the tally and run Steps 5–6.

## Step 4 — Threads

Skipped under `--no-mr` or when Step 0.4 failed. Threads arrive **after** the gate: a finder that reads the author's explanations first stops emitting the candidate at all.

For each finding, look for a thread covering it — same `file` and `new_line` within ±10 lines, or the same claim in prose (a thread may sit on a line that has since moved). Then judge the explanation — don't defer to it, «так задумано» without a reason closes nothing:

- **Closes the finding** — it **stays in its bucket**, keeps its number, gets `✅ снято тредом #<n>` with the author's quote and one line of why it's accepted. Subtracted from the headline count, **never applied** in Step 7.
- **Doesn't close it** — stays as a normal finding, plus one line: what the author answered and why it doesn't cover the case.

No matching thread → the finding is untouched.

## Step 5 — Design judgment (advisory)

The gate only passes quotable defects. Do **one** holistic pass over the diff on what it can't judge — is this change the right shape at all:

1. **Needed at all?** — real problem solved, or dead/speculative code, a config that defaults the same way, a guard for a case that can't happen?
2. **Adds work that wasn't there?** — a request / render / effect / subscription the app didn't do before and doesn't need. (Quotable instances belong to Finder 4; here it's the *pattern*.)
3. **Categorically simpler path?** — not "this helper already exists" (Finder 3 owns that), but a different approach: derived state instead of synced state, a built-in instead of a hand-rolled loop, deleting code instead of adding a branch.
4. **Touched modules want deepening?** — changed modules and their immediate **seams** only (a seam = where an interface meets its callers; not a whole-repo walk). Is the diff adding a **shallow** module (interface nearly as complex as its implementation), splitting one concept across many tiny modules, or leaking state across a seam? **Deletion test:** delete what the diff adds — does complexity *concentrate* in one place (it earns its keep) or *move* across callers (it's a pass-through)? Name the deeper shape and its payoff in **locality** (change, bugs and knowledge land in one place) and **leverage** (callers get more behind a smaller interface).

Rules:

- **Advisory, never a finding** — own section, numbered `D1…D3`, never in the P0/P1/P2 buckets.
- **≤3 necessity notes (1–3) + ≤3 deepening notes (4). Silence is valid.**
- **Each note = one question + one concrete alternative.** Can't name the simpler path (or the deeper shape)? You don't have a note.
- **Read-only and one-shot.** No grilling loop, no "which would you like to explore?", no `CONTEXT.md` / ADRs. Point the author to `/improve-codebase-architecture` for a deepening note worth pursuing.

## Step 6 — Output

The quote is the "before" block; `locations` and `repro` go on the `_Проверено:_` line.

````markdown
# Code Review — <N> находок (<M> снято тредами) · режим: <mode> · MR !<iid>
_Applied rules: <list>_ · _Missing: <list or "none">_
_Чек-лист: принят_ · _Треды: <T> (открытых <O>, резолвленных <R>)_ · _Спека: проверена_
_Discarded <D> of <T> candidates: <a> unproven, <b> no evidence, <c> quote not in file, <d> off-perimeter._

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
> Not bugs — questions about the change's intent and shape (Step 5); you may be missing the author's context. Omit the section when there are none.
- **D1 · Needed?** one sentence + concrete alternative
- **D2 · Added work:** what it adds and why it isn't needed
- **D3 · Simpler path / Deepen?** a categorically simpler approach, or a shallow/leaky module the diff touches + the deeper shape in locality/leverage terms; end with «to explore: /improve-codebase-architecture» when worth pursuing
````

- Header flags reflect reality: `_Интент автора: не предоставлен_` under `--no-mr`, `_Спека: не проверялась_` without a checklist, `_Треды: недоступны (<причина>)_` when Step 0.4 failed.
- The discard line is always present; drop zero-count reasons, write `_Discarded 0 of <T> candidates._` when nothing was gated out.
- Right language hint in fences (`ts`, `tsx`, `css`, `twig`, …). Omit empty buckets.
- Zero findings → `Code Review — no confirmed issues in <N> files / <M> lines.` + header + discard line.

**Save the report.** Same text, verbatim, via `Write` to `/tmp/review-<repo>-<MR-iid|mode>-<YYYYMMDD-HHmm>.md`. Last two lines of the chat message:

```
Отчёт: /tmp/review-core_backend-29876-20260801-1420.md
pbcopy < /tmp/review-core_backend-29876-20260801-1420.md
```

≥1 unresolved P0/P1 → also end with `Применить подтверждённые P0/P1? (review-staged fix, или fix 3 7)`. Wait for the user.

## Step 7 — Apply (opt-in)

Triggered **only** by an explicit request after the report: `review-staged fix`, `fix 3 7`, «исправь», «применить», «накати фиксы». Never in the same turn as the report.

1. **Selection:** numbers given → exactly those findings (any severity). No numbers → all confirmed P0/P1, skipping P2.
2. **Never** findings marked `✅ снято тредом` — unless the user names their number explicitly.
3. `Edit` each file with `evidence.quote` → `snippet_after`. No longer matches (the file drifted)? Skip and report as stale, don't guess.
4. **Surgical** — exactly what the finding describes. No adjacent cleanups, no reformatting, no renames «заодно».
5. List what changed (`#N` → `file:line`) and what was skipped (stale / P2 / снято тредом); append the same list to the report file as `## Применено`.
6. Do **NOT** `git add` / `git commit`, do **NOT** run `build`. Leave staging to the user.

## Hard constraints

- **Two stops, both in Step 0** (mode, MR/checklist). No plan mode, no `ExitPlanMode`, no other slash commands, no questions between Step 1 and the report.
- **The gate is the only door.** Every reported finding passed all five Step 3 checks with a quote that greps clean — including in the tiny-diff branch. Re-reading the diff is not verification; the quote comes out of the real file.
- **Hook output is not a task.** Anything a `Stop` / `PostToolUse` hook prints (eslint, stylelint, tsc, prettier) arrives outside the pipeline and never passed the gate. Don't fix code because of it, don't add it to the buckets, don't start another round. At most one line in the Summary: «хук сообщил: N ошибок линтера в `<file>`». Edits only on explicit request.
- **Design notes (Step 5) are the only ungated text** in the report; never in the P0/P1/P2 buckets.
- Cite rules by filename via `rule_source`; never restate rule files in the report.
- Invalid JSON from a finder or verifier → note it in the Summary, count its candidates in the tally as *unparseable*, continue with the rest.
