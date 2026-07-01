---
name: review-staged
description: Parallel review of the STAGED git diff by file-aware reviewers that verify every finding against the real files before it reaches you — unproven guesses are dropped at the source, not in a separate pass. Severity buckets, code-snippet fixes, and an advisory design pass. Inline read-only by default; applies confirmed P0/P1 only when you explicitly ask (`review-staged fix` or «исправь/применить» after the report). NOT the built-in `/code-review` (which reviews the whole branch and has `--fix`/`ultra`) — this one is scoped to the staged diff and rule-driven. Use when the user says «ревью стейджа», «review staged», `/review-staged`, or wants a safety/architecture/style/integration/performance audit of the staged changes against the repo's rule files.
---

# review-staged — file-aware review of the staged diff

One wave over the staged diff (`git diff --staged`), by reviewers that **prove before they report**.

Each reviewer has file access and carries its own burden of proof: it finds a candidate in the diff, opens the real file, checks the one assumption it rests on, and emits it **only if it can cite a `file:line` proving the problem is real**. Uncertain gets dropped silently. A diff-only guess can't tell a real null-deref from one a guard two lines up already handles — reading the file settles it before anything reaches the report, so no separate skeptic pass is needed.

```
Step 0  GROUND    load rules + manifest, filter the diff
Step 1  REVIEW    4 file-aware reviewers, each finds → verifies against real files → emits only proven findings
Step 2  MERGE     dedup + namespace + a light main-context cross-check of the P0/P1 evidence
Step 2.5 DESIGN   main context judges necessity / simpler path → advisory notes
Step 3  OUTPUT    verified findings only, each with its evidence
Step 4  APPLY     (opt-in) edit confirmed P0/P1 only when the user asks
```

Output INLINE — no plan mode, no clarifying questions. **Read-only by default**; writing files happens only in the opt-in Step 4 when the user explicitly asks to apply fixes.

## Step 0 — Ground

Discover the rule sources before spawning anything — the rule files are the source of truth, never hard-code rules into the charters.

1. Load (in order, skip missing): `CLAUDE.md` at repo root + any nested `CLAUDE.md` it references; `AGENTS.md` + nested; all `~/.claude/rules/*.md`; all `docs/rules/*.md`.
2. Build a **manifest** of which sources loaded and which were missing — both go in the report header. If zero rule files load, fall back to universal mode (correctness / security / performance only) and tag every `rule_source` as `"universal"`.
3. Get the reviewable **file list**: `rtk run git diff --staged --name-only`. If empty, stop and report "no staged changes". **Filter out non-reviewable paths** — binary files, lockfiles (`*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`), generated/minified output (`*.min.*`, `dist/`, `build/`, `.next/`, `out/`), snapshots (`*.snap`), vendored dirs. Don't pass their hunks to anyone.
4. Fetch each reviewable file's diff **raw and separately** — `rtk run git diff --staged -- <file>` — then concatenate into the full staged diff. **Why `rtk run`:** the Bash hook rewrites bare `git diff` into rtk's *condensed* diff (only changed lines), which silently drops context and truncates. `rtk run` is the unfiltered passthrough the hook leaves untouched; per-file fetch also chunks the diff so no oversized blob gets truncated.

Pass the full (per-file, raw) staged diff, the rule files (verbatim), and the manifest to every reviewer. Don't make reviewers re-derive rules from memory — expect them to read the code.

## Step 1 — Review

Spawn **4 reviewers in a single message** (multiple Task calls), each with **Read, Grep and LSP access**. Each gets the diff, the rule files inline, the manifest, and its charter. Each returns a JSON array of **verified findings** — nothing else. Each reviewer is its own skeptic: it `Read`s the enclosing function and `hover`/`goToDefinition`s the symbol to settle each candidate before emitting it.

**Tiny-diff branch:** if the filtered diff touches ≤3 files OR <100 changed lines, skip the fan-out — do one inline pass yourself covering all 4 charters, opening the touched files to verify each finding exactly as a reviewer would. Same output format, same prove-before-report discipline. The fan-out isn't worth the overhead on small diffs, but the file-aware verification still is.

**Common prelude — prepend to every reviewer prompt:**

> - **Rules = the loaded files only.** Don't re-derive rules from memory or training. Flag a violation only if it's (a) traceable to a specific line in the loaded rules, or (b) a universal correctness / security / performance principle. Cite the rule's file name in `rule_source`.
> - **Diff-scope only.** Review only added/modified hunks. No adjacent code, no pre-existing violations, no "while you're here" cleanups. Exception: a diff-line that depends on broken adjacent code the diff also touches.
> - **Trust the linter.** Skip anything eslint / stylelint / tsc already covers — assume CI catches it. Focus on what the linter can't see.
> - **Prove it, then report it — you are your own skeptic.** You have Read/Grep/LSP; use them. For every candidate you spot in the diff, name the single assumption it rests on (e.g. "`user` can be null here", "`n` is large", "no shared helper exists"), then open the *real files* and check that assumption **before** you emit anything. Report a finding only when you can cite a concrete `file:line` in the real code that proves the problem is real. If a caller or guard refutes it → drop it silently. Uncertain → drop it. A diff-only guess with no file:line evidence is exactly the noise this review exists to eliminate; an unverified finding is worse than a missed one. Put the proof in the `evidence` field.
> - **Read narrowly, not the whole repo.** Open only what the assumption needs — the enclosing function, the caller, the type def, the guard, the neighbouring slice. For ts/js/tsx, php, rust, go check *semantics* via LSP, not by grepping the symbol name: `findReferences` / `incomingCalls` for who calls it, `goToDefinition` for where it resolves, `hover` for its real type. Grep is only a locator to find a symbol's position. Prefer a targeted `hover`/`goToDefinition` over dumping a whole `documentSymbol`.

Finding schema:

```json
{
  "id": "correctness-1",
  "severity": "P0" | "P1" | "P2",
  "file": "path/to/file.ts",
  "line": 123,
  "issue": "one-sentence description of the problem",
  "rule_source": "architecture.md | code-style.md | universal | ...",
  "snippet_before": "exact code from the diff that has the problem",
  "snippet_after": "concrete fixed version of the snippet",
  "fix": "one-sentence rationale for the fix",
  "evidence": "the file:line you actually read that proves this is real, e.g. 'no guard at userCard.tsx:40-58; caller passes raw props at list.tsx:12'"
}
```

`snippet_before` / `snippet_after` mandatory for P0/P1, optional for P2 (omit when a rename or formatting tweak makes the fix obvious). **`evidence` is mandatory for every finding** — it's the `file:line` you read to prove the problem. A finding you can't back with evidence you actually read is a finding you must drop, not emit.

Severity:

- **P0** — bug, regression, security issue, data loss, type error, broken build, quadratic-or-worse on a large-n path. Must fix before merge.
- **P1** — architecture violation, integration risk, missing edge case, quadratic on bounded-but-growable input, rule violation that will cause pain. Should fix.
- **P2** — style, naming, duplication, micro-inefficiency, drift. Nice to fix.

### Reviewer 1 — Safety & Correctness

Correctness/security the rule files don't cover: logic bugs, null/undefined, off-by-one, races, unhandled rejections, async/await misuse, type errors, unsafe casts, XSS/injection, secrets in code, unsafe `dangerouslySetInnerHTML`, unvalidated input crossing trust boundaries, broken contracts (changed return types without updating callers), edge cases (empty arrays, long unbroken strings, overflow, missing fallbacks). Before emitting: read the enclosing function and the guards/early-returns around the suspect line, and for a "broken contract" use `findReferences` to check whether callers actually break.

### Reviewer 2 — Architecture & Integration

Structure and system-fit, per the loaded rules — cite the file in `rule_source`, don't invent rules:

- **Layer / slice / module boundaries** the diff contradicts. Verify by reading the neighbouring modules the rule governs, not by guessing from the diff.
- **Runtime + ship-time interactions**: i18n, cache/query keys, form-abandonment, lazy-loading, browser support, lifecycle ownership, error bubbling.
- **i18n key lifecycle** (universal): new keys missing from locale files (check the locale file exists and lacks the key), removed keys still referenced (`findReferences`), hard-coded user-facing strings.
- **Deployment** (universal): feature flags, env vars, migration order, backward-incompatible payloads, breaking public-contract changes callers in the diff don't update — confirm the caller break by reading the caller.

### Reviewer 3 — Style, Naming & Duplication

Style/naming the linter can't catch, per the loaded rules. Plus **cross-slice duplication** — logic repeated where a shared module belongs; verify by actually locating the existing helper (`workspaceSymbol` / Grep) before claiming one exists, since "a helper already exists" is only a finding if the helper is real. Plus magic numbers/strings that are repeated or semantically opaque.

### Reviewer 4 — Performance & Complexity

Strict algorithmic complexity — clean-looking code hiding O(n²)+ — plus avoidable sequential I/O **and avoidable work the diff itself introduces**: a request, a re-render, an effect or a query the app didn't do before and doesn't need. Each P0/P1 names the delta (e.g. `O(n*m) → O(n+m)`, or `+1 request → reuse existing query`); `snippet_after` shows the Set/Map, `Promise.all`, memo or reuse fix. Before emitting a complexity finding, verify n isn't provably tiny/fixed by reading where the collection comes from — a quadratic over a 3-element constant is not a finding.

- **Nested membership lookups** — `.find` / `.includes` / `.indexOf` / `.some` inside `.map` / `.filter` / `for`. Lift the inner collection into a `Set`/`Map` once.
- **Chained passes** — `.filter().map().find()` where a single pass or early exit would do.
- **`.sort()` in render or per-event** — sort once, memoize, or sort on write.
- **Heavy construction in a loop** — `new RegExp`, `JSON.parse(JSON.stringify(...))`, `new Date` from a constant. Hoist out.
- **Sequential `await` in a loop** with independent iterations → `Promise.all` / `Promise.allSettled`.
- **N+1** API/DB calls where one batched fetch exists.
- **Recursive tree/set building without memoization** on non-trivial input.
- **Quadratic string building** — `str += ...` over large n where chunks + `join` is linear.

**Added runtime work (did the diff make the app do *more* than before?)** — flag only where eslint-plugin-react-hooks / the linter can't already see it:

- **Redundant request** — a new `fetch`/query for data already fetched (existing query, cache, props, parent). Confirm the existing source exists before claiming redundancy.
- **Extra re-render** — new state/context, or an inline object/array/callback passed as a prop, that re-renders a subtree where lifting state, a ref, or memoization avoids it.
- **Over-firing effect** — `useEffect` whose dep is an object/array recreated each render (or too broad), causing repeat fetches/subscriptions; stabilize the dep or move the work out.
- **Recompute each render** — non-trivial value rebuilt every render that `useMemo` / hoisting / deriving-once avoids.

## Step 2 — Merge & cross-check

Parse the 4 arrays into one list — this is main-context bookkeeping plus one targeted audit, **not** a second review wave:

1. **Re-namespace** each `id` to be globally unique (`correctness-1`, `perf-2`).
2. **Deduplicate** by `(file, line, issue similarity)` — keep the higher severity, prefer the more concrete snippet and the more specific `rule_source`, merge the `evidence`.
3. **Cross-check the P0/P1 only.** These are the findings that get applied and carry the most risk, so give each one a five-second sanity read: does its `evidence` cite a real `file:line`, and does that citation actually prove the claim? You already hold the diff and the rules. If a P0/P1 has vague or missing evidence, either downgrade it or — if it's cheap and worth it — `Read` that one file yourself to settle it. Drop it if it doesn't hold. P2s ride on their reviewer's evidence without this audit; they're low-stakes and it's not worth the time. This costs a few reads on the findings that matter, not a full pass over everything.
4. **Sort** by file, then line. Group by severity for output (P0 → P1 → P2).

If the merged list is empty, skip to reporting no issues (still run Step 2.5).

## Step 2.5 — Design judgment (advisory)

The review above only catches *falsifiable, line-pinnable* problems. It can't judge the gestalt: **is this change the right shape at all?** That's taste, not a provable bug — a reviewer would drop it for lack of `file:line` evidence — so it's owned by **you, the main context** (you already hold the diff, rules and verified findings).

After Step 2, do **one** holistic pass over the whole diff and ask a small, fixed set:

1. **Is the change needed at all?** — does it solve a real problem, or is it dead/speculative code, a config that defaults the same way, a guard for a case that can't happen?
2. **Does it add work that wasn't there?** — a request / render / effect / subscription the app didn't do before and doesn't need. (Concrete, line-pinnable instances belong to Reviewer 4; here you flag the *pattern* even when you can't pin one line.)
3. **Is there a categorically simpler path?** — not "this helper already exists" (Reviewer 3 owns that), but "this whole approach could be replaced by a simpler one" — derived state instead of synced state, a built-in instead of a hand-rolled loop, deleting code instead of adding a branch.
4. **Could touched modules be deepened?** — judge the **changed modules and their immediate seams only** (a *seam* = where an interface meets its callers, a place behaviour can be swapped without editing in place; not a whole-repo walk). Is the diff adding a **shallow** module — interface nearly as complex as its implementation — splitting one concept across many tiny modules, or leaking state across a seam? Run the **deletion test** on each: if you deleted what the diff adds, would complexity *concentrate* in one place (it earns its keep) or just *move* across callers (it's a pass-through)? For every module that wants deepening, name the deeper shape — fewer, deeper modules behind a smaller interface — and the payoff: **locality** (change, bugs and knowledge land in one place) and **leverage** (callers get more behind a smaller interface), plus testability. More than one module may qualify — report the most valuable first.

This lens (deep vs shallow modules, seams, the deletion test) is the one `improve-codebase-architecture` skill runs at repo scale; the definitions above are inlined here so this pass is self-contained.

Rules for this pass:

- **Advisory, never a finding** — own tagged section, never in the P0/P1/P2 buckets.
- **At most 3 design-necessity notes (questions 1–3) plus at most 3 deepening notes (question 4); silence is valid for both** — don't manufacture notes to fill space.
- **Each note = one question to the author + one concrete alternative.** No vague "consider refactoring"; if you can't name the simpler path (or, for #4, the deeper shape), you don't have a note.
- **Stay read-only and one-shot.** Don't drop into a grilling loop, don't ask "which would you like to explore?", and don't write `CONTEXT.md` / ADRs — those are `improve-codebase-architecture` skill's job. For a deepening note worth pursuing interactively, end it by pointing the author to `/improve-codebase-architecture`.

## Step 3 — Output

Every finding carries the evidence its reviewer proved it with. Render with before/after snippets when present, and always include the `_Проверено:_` line (the finding's `evidence`) so the user can audit the verification.

````markdown
# Code Review — <N> confirmed findings
_Applied rules: <list>_ · _Missing: <list or "none">_
_Reviewers: 4 · findings: <N>_

## P0 — Must fix (<count>)

### `file.ts:123` — issue description

```ts
// before
<snippet_before>
```
```ts
// after
<snippet_after>
```

_Why:_ <rationale> _(source: <rule_source>)_.
_Проверено:_ <evidence — the file:line the reviewer read that proves this>.

## P1 — Should fix (<count>)
Same shape as P0.

## P2 — Nice to fix (<count>)
- **`file.ts:78`** — issue. _Fix:_ <one-liner> _(source: <rule_source>)_. _Проверено:_ <evidence>.

## Summary
<2-3 sentences: risk level, headline concerns, merge recommendation.>

## 💭 Design notes (advisory, unverified)
> Not bugs — questions about the change's intent and shape (Step 2.5); you may be missing the author's context. Up to 3 necessity notes + up to 3 deepening notes, each a question + a concrete alternative. Omit the whole section when there are none.
- **Needed?** one sentence + concrete alternative
- **Added work:** what it adds and why it isn't needed
- **Simpler path:** a categorically simpler approach
- **Deepen?** a shallow/leaky module the diff touches + the deeper shape, in locality/leverage terms — one bullet per module, most valuable first, up to 3; end with «to explore: /improve-codebase-architecture» when worth pursuing
````

Use the right language hint in fences (`ts`, `tsx`, `css`, `twig`, …). Omit empty buckets. If zero findings survive, output `Code Review — no confirmed issues in <N> files / <M> lines.` plus the manifest header.

After the report, when there is ≥1 P0/P1, end with one line offering to apply: `Применить подтверждённые P0/P1? (review-staged fix)`. Do not apply now — wait for the user.

## Step 4 — Apply (opt-in)

Triggered **only** by an explicit user request after the report: `review-staged fix`, or a reply like «исправь», «применить», «накати фиксы». Never apply in the same turn as the report, and never without this signal.

When triggered:

1. Apply **only P0/P1** findings — skip P2 unless the user names them.
2. For each, `Edit` the file using the reviewer-validated `snippet_before` → `snippet_after`. If `snippet_before` no longer matches the file (it drifted since the review), skip that finding and report it as stale rather than guessing.
3. **Surgical only** — change exactly what the finding describes. No adjacent cleanups, no reformatting, no renames «заодно».
4. After editing, list what changed (`file:line` per applied finding) and what was skipped (stale / P2).
5. Honor git rules: do **NOT** `git add` / `git commit`, do **NOT** run `build`. Leave staging to the user.

## Hard constraints

- INLINE: no plan mode, no `ExitPlanMode`/`AskUserQuestion`, no other slash commands. **Read-only by default — no file writes during Steps 0–3.** Code changes happen only in the opt-in Step 4, after an explicit user request, and only on P0/P1.
- **Never show an unverified finding.** The proof lives in each finding's `evidence` (a `file:line` the reviewer actually read); a finding without it must be dropped by its reviewer, not surfaced. If a reviewer emits a finding with empty/fabricated evidence and it slips through, drop it in Step 2. Re-reading the diff is not verification — the reviewer must inspect the real files (`Read` + `LSP`, `Grep` only to locate).
- **Design notes (Step 2.5) are advisory** — the only unverified text in the report; never in the P0/P1/P2 buckets.
- Every finding's `file:line` must point at a diff-touched line; drop violators on merge.
- Cite rules by filename via `rule_source`; never restate rule files in the report.
- If a reviewer returns invalid JSON, note it in the Summary and continue with the rest.
