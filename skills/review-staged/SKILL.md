---
name: review-staged
description: Evidence-gated review of the STAGED git diff. Finders emit candidate issues, a verifier opens the real file and attaches a verbatim quote as evidence, and a mechanical gate discards every candidate whose evidence is empty, mis-quoted, or outside the perimeter (staged hunks + their direct importers). Reports confirmed findings plus a one-line tally of what was discarded and why. Severity buckets, code-snippet fixes, and an advisory design pass. Inline read-only by default; applies confirmed P0/P1 only when you explicitly ask (`review-staged fix` or «исправь/применить» after the report). NOT the built-in `/code-review` (which reviews the whole branch and has `--fix`/`ultra`) — this one is scoped to the staged diff and rule-driven. Use when the user says «ревью стейджа», «review staged», `/review-staged`, or wants a safety/architecture/style/integration/performance audit of the staged changes against the repo's rule files.
---

# review-staged — evidence-gated review of the staged diff

A finder emits a **candidate** (a claim). A verifier opens the real file and attaches **evidence** (a verbatim quote). The **gate** discards every candidate whose quote isn't in the file. Survivors are **findings**.

```
Step 0  GROUND    rules + manifest + perimeter
Step 1  FIND      4 finders → candidates {severity, file, line, claim}
Step 2  VERIFY    1 verifier per file → evidence (verbatim quote + repro command)
Step 3  GATE      discard unproven / evidence-less / mis-quoted / off-perimeter → findings + tally
Step 4  DESIGN    advisory pass over the whole diff
Step 5  OUTPUT    findings + the tally
Step 6  APPLY     (opt-in) edit confirmed P0/P1 on explicit request
```

INLINE — no plan mode, no clarifying questions. Read-only in Steps 0–5; files are written only in Step 6.

## The perimeter

**Perimeter = the staged hunks + the files that directly import them.** Finders read inside it, verifiers read inside it, the gate discards anything cited outside it.

Navigate by **name**, from a cited line to the one thing it depends on (guard, caller, type def): `goToDefinition` / `findReferences` / `incomingCalls` / `hover` for ts/js/tsx, php, rust, go; `Grep` on a literal symbol or import specifier when LSP can't reach. Grep returns a position, never a survey.

**Hard guardrail:** no repo-wide regex sweep hunting a pattern (`Grep` for `\.map\(.*\.find\(` across the tree, `find` over unrelated dirs) — it leaves the perimeter and produces claims about untouched code. Need to know who else does this? `findReferences` on the one symbol.

## Step 0 — Ground

1. **File list:** `rtk run git diff --staged --name-only`. Empty → stop, report "no staged changes". Filter out binaries, lockfiles (`*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`), generated output (`*.min.*`, `dist/`, `build/`, `.next/`, `out/`), snapshots (`*.snap`), vendored dirs.
2. **Perimeter:** the filtered list + each file's direct importers (one `Grep` per file on a literal import specifier, or `findReferences` on its exports). Write it down — Step 3 checks against it.
3. **Rules** (in order, skip missing): root `CLAUDE.md` + nested ones it references; `AGENTS.md` + nested; `~/.claude/rules/*.md`; `docs/rules/*.md`. Drop any rule file whose frontmatter `paths` globs match none of the reviewable files (no `paths` key = always applies) — otherwise a PHP rulebook rides into a TypeScript review.
4. **Manifest:** which sources loaded / were missing / were skipped as not applicable. All three go in the report header. Zero rules loaded → universal mode (correctness / security / performance only), every `rule_source` tagged `"universal"`.
5. **Diff:** `rtk run git diff --staged -- <file>` per file, concatenated. `rtk run` because the Bash hook rewrites bare `git diff` into a condensed diff that drops context and truncates; per-file also avoids oversized blobs.

## Step 1 — Find

Spawn **4 finders in a single message** (Read + Grep + LSP), each with the full diff, the rule files inline, the manifest, the perimeter, and its charter. Each returns a JSON array of candidates, nothing else.

**Tiny-diff branch:** ≤3 files OR <100 changed lines → run all four charters yourself inline. Steps 2 and 3 still run in full.

**Common prelude — prepend to every finder prompt:**

> - **Rules = the loaded files only.** No rules from memory. Flag a violation only if it's traceable to a line in the loaded rules, or is a universal correctness / security / performance principle. Name the rule file in `rule_source`.
> - **Diff-scope only.** Added/modified hunks. No adjacent code, no pre-existing violations, no "while you're here". Exception: a diff-line depending on broken adjacent code the diff also touches.
> - **Trust the linter.** Skip what eslint / stylelint / tsc covers.
> - **You emit candidates, not verdicts.** State the claim and the assumption it rests on ("`user` can be null here", "`n` is large", "no shared helper exists"). A verifier will back it with a quote or kill it — so spend your reads on *aiming* (a quick `hover` / `goToDefinition` that you're on the right line and symbol), not on proving. An assumption you already know is refuted, drop yourself.
> - **`file` + `line` must name a real position inside the perimeter.** A candidate the verifier can't locate dies in the gate.
> - [paste "The perimeter" section verbatim]

```json
{
  "severity": "P0" | "P1" | "P2",
  "file": "path/to/file.ts",
  "line": 123,
  "claim": "one sentence: what is wrong and why it breaks",
  "rule_source": "architecture.md | code-style.md | universal | ..."
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

## Step 2 — Verify

Group candidates by `file`, spawn **one verifier per file, all in a single message** (Read + Grep + LSP). Each gets that file's candidates, its staged diff, the perimeter, and this charter:

> `Read` the cited file around the cited line, check the assumption the claim rests on (the guard two lines up, the caller, the type, the real size of the collection), return each candidate with a `verdict` — and, when confirmed, `evidence`.
>
> - `evidence.quote` — the lines **copied verbatim** out of the file you just read, enough to make the problem visible (2–10 lines). Not paraphrased, not rebuilt from the diff, not re-indented. The gate greps this back into the file: a typed quote won't match and the candidate dies.
> - `evidence.locations` — the `file:line` ranges you opened, including the corroborating one (`"no guard at userCard.tsx:40-58; caller passes raw props at list.tsx:12"`).
> - `evidence.repro` — a command that would surface the problem when one exists (`yarn jest x.test.ts -t 'name'`, `tsc --noEmit`, `node -e "…"`, a curl, a URL + click path). **Report it, don't run it.** Omit for claims no command surfaces (naming, duplication, layering).
> - `verdict: "refuted"` when the file shows it can't happen — name the line that refutes it. `"unproven"` when you can't settle it. Both are correct answers and cost nothing; a `confirmed` backed by a quote you didn't copy is the one real failure.
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

- **Namespace** each with a stable id (`correctness-1`, `perf-2`).
- **Dedup** by `(file, line, claim similarity)` — keep the higher severity, the more specific `rule_source`, the longer quote; merge `evidence.locations`.
- **Sort** by file, then line; group P0 → P1 → P2.

Carry the tally (count + reason breakdown) into the report. Zero findings → still report the tally and run Step 4.

## Step 4 — Design judgment (advisory)

The gate only passes quotable defects. Do **one** holistic pass over the diff on what it can't judge — is this change the right shape at all:

1. **Needed at all?** — real problem solved, or dead/speculative code, a config that defaults the same way, a guard for a case that can't happen?
2. **Adds work that wasn't there?** — a request / render / effect / subscription the app didn't do before and doesn't need. (Quotable instances belong to Finder 4; here it's the *pattern*, unpinnable to one line.)
3. **Categorically simpler path?** — not "this helper already exists" (Finder 3 owns that), but a different approach: derived state instead of synced state, a built-in instead of a hand-rolled loop, deleting code instead of adding a branch.
4. **Touched modules want deepening?** — changed modules and their immediate **seams** only (a seam = where an interface meets its callers; not a whole-repo walk). Is the diff adding a **shallow** module (interface nearly as complex as its implementation), splitting one concept across many tiny modules, or leaking state across a seam? **Deletion test:** delete what the diff adds — does complexity *concentrate* in one place (it earns its keep) or *move* across callers (it's a pass-through)? Name the deeper shape and its payoff in **locality** (change, bugs and knowledge land in one place) and **leverage** (callers get more behind a smaller interface).

Rules:

- **Advisory, never a finding** — own section, never in the P0/P1/P2 buckets.
- **≤3 necessity notes (1–3) + ≤3 deepening notes (4). Silence is valid** — don't manufacture notes to fill space.
- **Each note = one question + one concrete alternative.** Can't name the simpler path (or the deeper shape)? You don't have a note.
- **Read-only and one-shot.** No grilling loop, no "which would you like to explore?", no `CONTEXT.md` / ADRs. Point the author to `/improve-codebase-architecture` for a deepening note worth pursuing.

## Step 5 — Output

The quote is the "before" block; `locations` and `repro` go on the `_Проверено:_` line.

````markdown
# Code Review — <N> confirmed findings
_Applied rules: <list>_ · _Missing: <list or "none">_
_Discarded <D> of <T> candidates: <a> unproven, <b> no evidence, <c> quote not in file, <d> off-perimeter._

## P0 — Must fix (<count>)

### `file.ts:123` — claim

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

## P1 — Should fix (<count>)
Same shape as P0.

## P2 — Nice to fix (<count>)
- **`file.ts:78`** — claim. _Fix:_ <one-liner> _(source: <rule_source>)_. _Проверено:_ <evidence.locations>.

## Summary
<2-3 sentences: risk level, headline concerns, merge recommendation.>

## 💭 Design notes (advisory, ungated)
> Not bugs — questions about the change's intent and shape (Step 4); you may be missing the author's context. Omit the section when there are none.
- **Needed?** one sentence + concrete alternative
- **Added work:** what it adds and why it isn't needed
- **Simpler path:** a categorically simpler approach
- **Deepen?** a shallow/leaky module the diff touches + the deeper shape, in locality/leverage terms — one bullet per module, most valuable first, ≤3; end with «to explore: /improve-codebase-architecture» when worth pursuing
````

- The discard line is always present; drop zero-count reasons, write `_Discarded 0 of <T> candidates._` when nothing was gated out.
- Right language hint in fences (`ts`, `tsx`, `css`, `twig`, …). Omit empty buckets.
- Zero findings → `Code Review — no confirmed issues in <N> files / <M> lines.` + manifest header + discard line.
- ≥1 P0/P1 → end with `Применить подтверждённые P0/P1? (review-staged fix)`. Wait for the user.

## Step 6 — Apply (opt-in)

Triggered **only** by an explicit request after the report: `review-staged fix`, «исправь», «применить», «накати фиксы». Never in the same turn as the report.

1. **P0/P1 only** — skip P2 unless the user names them.
2. `Edit` each file with `evidence.quote` → `snippet_after`. No longer matches (the file drifted)? Skip and report as stale, don't guess.
3. **Surgical** — exactly what the finding describes. No adjacent cleanups, no reformatting, no renames «заодно».
4. List what changed (`file:line` per applied finding) and what was skipped (stale / P2).
5. Do **NOT** `git add` / `git commit`, do **NOT** run `build`. Leave staging to the user.

## Hard constraints

- INLINE: no plan mode, no `ExitPlanMode`/`AskUserQuestion`, no other slash commands. Read-only through Step 5.
- **The gate is the only door.** Every reported finding passed all five Step 3 checks with a quote that greps clean — including in the tiny-diff branch. Re-reading the diff is not verification; the quote comes out of the real file.
- **Design notes (Step 4) are the only ungated text** in the report; never in the P0/P1/P2 buckets.
- Cite rules by filename via `rule_source`; never restate rule files in the report.
- Invalid JSON from a finder or verifier → note it in the Summary, count its candidates in the tally as *unparseable*, continue with the rest.
