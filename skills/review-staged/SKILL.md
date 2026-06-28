---
name: review-staged
description: Parallel review of the STAGED git diff with an adversarial verification pass, severity buckets and code-snippet fixes. Findings are confirmed against the real files before they reach you — refuted ones are dropped. Inline read-only by default; applies confirmed P0/P1 only when you explicitly ask (`review-staged fix` or «исправь/применить» after the report). NOT the built-in `/code-review` (which reviews the whole branch and has `--fix`/`ultra`) — this one is scoped to the staged diff and rule-driven. Use when the user says «ревью стейджа», «review staged», `/review-staged`, or wants a safety/architecture/style/integration/performance audit of the staged changes against the repo's rule files.
---

# review-staged — adversarial review of the staged diff

Two waves over the staged diff (`git diff --staged`):

- **FIND** — 5 parallel finder subagents see the diff alone and produce *candidates*. A broad, cheap net: they over-produce on purpose.
- **VERIFY** — one skeptic subagent reads the real files and tries to *refute* each candidate. Only confirmed ones survive.

The finders and the critic are different agents on purpose — the critic has the file context the diff-only finders lack. Output INLINE — no plan mode, no clarifying questions. **Read-only by default**; writing files happens only in the opt-in Step 5 when the user explicitly asks to apply fixes. After verification the main context adds one **advisory** design pass (Step 3.5), kept separate from the verified findings.

```
Step 0  GROUND   load rules + manifest, filter the diff
Step 1  FIND     5 finders, diff-only → candidates
Step 2  MERGE    dedup + namespace ids
Step 3  VERIFY   one skeptic reads files, refutes → keep confirmed
Step 3.5 DESIGN  main context judges necessity / simpler path → advisory notes
Step 4  OUTPUT   confirmed findings only, each with its evidence
Step 5  APPLY    (opt-in) edit confirmed P0/P1 only when the user asks
```

## Step 0 — Ground

Discover the rule sources before spawning anything — the rule files are the source of truth, never hard-code rules into the charters.

1. Load (in order, skip missing): `CLAUDE.md` at repo root + any nested `CLAUDE.md` it references; `AGENTS.md` + nested; all `~/.claude/rules/*.md`; all `docs/rules/*.md`.
2. Build a **manifest** of which sources loaded and which were missing — both go in the report header. If zero rule files load, fall back to universal mode (correctness / security / performance only) and tag every `rule_source` as `"universal"`.
3. Get the reviewable **file list**: `rtk run git diff --staged --name-only`. If empty, stop and report "no staged changes". **Filter out non-reviewable paths** — binary files, lockfiles (`*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`), generated/minified output (`*.min.*`, `dist/`, `build/`, `.next/`, `out/`), snapshots (`*.snap`), vendored dirs. Don't pass their hunks to anyone.
4. Fetch each reviewable file's diff **raw and separately** — `rtk run git diff --staged -- <file>` — then concatenate into the full staged diff. **Why `rtk run`:** the Bash hook rewrites bare `git diff` into rtk's *condensed* diff (only changed lines), which silently drops context and truncates — that's what broke this review before. `rtk run` is the unfiltered passthrough the hook leaves untouched; per-file fetch also chunks the diff so no oversized blob gets truncated.

Pass the full (per-file, raw) staged diff, the rule files (verbatim), and the manifest to every subagent. Don't make subagents re-read files or re-derive rules from memory.

## Step 1 — FIND

Spawn 5 finders in a single message (multiple Task calls). Each gets the diff, the rule files inline, the manifest, and its charter. Each returns a JSON array of **candidates** — nothing else. Candidates are unverified, so finders should report anything plausible rather than self-censor; Step 3 filters the noise.

**Tiny-diff branch:** if the filtered diff touches ≤3 files OR <100 changed lines, skip the fan-out — do one inline pass covering all 5 charters, then run Step 3's verification inline yourself (open the touched files, refute, keep confirmed). Same output format. The fan-out isn't worth it on small diffs, but the verify discipline still is.

**Common prelude — prepend to every finder prompt:**

> - **Rules = the loaded files only.** Don't re-derive rules from memory or training. Flag a violation only if it's (a) traceable to a specific line in the loaded rules, or (b) a universal correctness / security / performance principle. Cite the rule's file name.
> - **Diff-scope only.** Review only added/modified hunks. No adjacent code, no pre-existing violations, no "while you're here" cleanups. Exception: a diff-line that depends on broken adjacent code the diff also touches.
> - **Trust the linter.** Skip anything eslint / stylelint / tsc already covers — assume CI catches it. Focus on what the linter can't see.

Candidate schema:

```json
{
  "id": "c1",
  "severity": "P0" | "P1" | "P2",
  "file": "path/to/file.ts",
  "line": 123,
  "issue": "one-sentence description of the problem",
  "rule_source": "architecture.md | code-style.md | universal | ...",
  "snippet_before": "exact code from the diff that has the problem",
  "snippet_after": "concrete fixed version of the snippet",
  "fix": "one-sentence rationale for the fix",
  "assumption": "the unverified assumption this rests on, e.g. 'user can be null here'"
}
```

`snippet_before` / `snippet_after` mandatory for P0/P1, optional for P2 (omit when a rename or formatting tweak makes the fix obvious). `assumption` is **mandatory** — it names the one thing the skeptic must check against the real files. A candidate whose assumption can't be stated is too vague to verify; don't report it.

Severity:

- **P0** — bug, regression, security issue, data loss, type error, broken build, quadratic-or-worse on a large-n path. Must fix before merge.
- **P1** — architecture violation, integration risk, missing edge case, quadratic on bounded-but-growable input, rule violation that will cause pain. Should fix.
- **P2** — style, naming, duplication, micro-inefficiency, drift. Nice to fix.

### Finder 1 — Safety & Correctness

Correctness/security the rule files don't cover: logic bugs, null/undefined, off-by-one, races, unhandled rejections, async/await misuse, type errors, unsafe casts, XSS/injection, secrets in code, unsafe `dangerouslySetInnerHTML`, unvalidated input crossing trust boundaries, broken contracts (changed return types without updating callers), edge cases (empty arrays, long unbroken strings, overflow, missing fallbacks).

### Finder 2 — Architecture

Layer / slice / module boundaries as defined in the loaded rules. Flag structural choices in the diff that contradict a loaded rule; cite the file in `rule_source`. Don't invent rules not in the sources.

### Finder 3 — Style, Naming & Duplication

Style/naming the linter can't catch, per the loaded rules. Plus cross-slice duplication (logic repeated where a shared module belongs) and magic numbers/strings that are repeated or semantically opaque.

### Finder 4 — Integration & Deployment

- Runtime + ship-time interactions per the loaded rules (i18n, cache/query keys, form-abandonment, lazy-loading, browser support, lifecycle ownership, error bubbling). Cite the file; don't invent rules.
- **i18n key lifecycle** (universal): new keys missing from locale files, removed keys still referenced, hard-coded user-facing strings.
- **Deployment** (universal): feature flags, env vars, migration order, backward-incompatible payloads, breaking public-contract changes callers in the diff don't update.

### Finder 5 — Performance & Complexity

Strict algorithmic complexity — clean-looking code hiding O(n²)+ — plus avoidable sequential I/O **and avoidable work the diff itself introduces**: a request, a re-render, an effect or a query the app didn't do before and doesn't need. Each P0/P1 names the delta (e.g. `O(n*m) → O(n+m)`, or `+1 request → reuse existing query`); `snippet_after` shows the Set/Map, `Promise.all`, memo or reuse fix.

- **Nested membership lookups** — `.find` / `.includes` / `.indexOf` / `.some` inside `.map` / `.filter` / `for`. Lift the inner collection into a `Set`/`Map` once.
- **Chained passes** — `.filter().map().find()` where a single pass or early exit would do.
- **`.sort()` in render or per-event** — sort once, memoize, or sort on write.
- **Heavy construction in a loop** — `new RegExp`, `JSON.parse(JSON.stringify(...))`, `new Date` from a constant. Hoist out.
- **Sequential `await` in a loop** with independent iterations → `Promise.all` / `Promise.allSettled`.
- **N+1** API/DB calls where one batched fetch exists.
- **Recursive tree/set building without memoization** on non-trivial input.
- **Quadratic string building** — `str += ...` over large n where chunks + `join` is linear.

**Added runtime work (did the diff make the app do *more* than before?)** — flag only where eslint-plugin-react-hooks / the linter can't already see it:

- **Redundant request** — a new `fetch`/query for data already fetched (existing query, cache, props, parent). Reuse the existing source instead of adding a second round-trip.
- **Extra re-render** — new state/context, or an inline object/array/callback passed as a prop, that re-renders a subtree on every parent render where lifting state, a ref, or memoization avoids it.
- **Over-firing effect** — `useEffect` whose dep is an object/array recreated each render (or too broad), causing repeat fetches/subscriptions; stabilize the dep or move the work out of the effect.
- **Recompute each render** — non-trivial value rebuilt every render that `useMemo` / hoisting / deriving-once avoids.

## Step 2 — Merge candidates

Parse the 5 arrays into one list:

1. **Re-namespace** each `id` to be globally unique (`s1-c1`, `s3-c2`) so the skeptic can reference them.
2. **Deduplicate** by `(file, line, issue similarity)` — keep the higher severity, prefer the more concrete snippet and the more specific `rule_source`, merge meaningful `assumption`s.
3. **Sort** by file, then line (severity grouping happens after verification).

If the merged list is empty, skip Step 3 and report no issues.

## Step 3 — Verify

Spawn **a single skeptic subagent** (one Task call) with **Read, Grep and LSP access**. One skeptic per file is too narrow and misses cross-file guarantees — one skeptic that sees every candidate and can read any file has the widest context.

It receives: the diff, the rule files, the manifest, and the full merged candidate list (`id`, `assumption`, `file`, `line`, `snippet_before`, `rule_source`).

**Skeptic charter:**

> You are an adversarial reviewer. Your job is to **refute** each candidate, not confirm it. Each rests on an `assumption` — check it against the *real files*, not the diff.
>
> For each candidate:
> 1. `Read` the file and its surroundings — enclosing function, callers, type defs, guards, early returns the diff-only finder couldn't see. For ts/js/tsx, php, rust, go check the *semantics* via `LSP`, not by grepping the symbol name: `findReferences`/`incomingCalls` for who calls it, `goToDefinition` for where it resolves, `hover` for its real type. Grep is only a locator to find a symbol's position.
> 2. Decide whether the `assumption` holds. "Possible null deref" → refuted if a caller/guard guarantees non-null. "O(n²)" → refuted if n is provably tiny/fixed. Rule violation → refuted if the cited rule doesn't apply or the linter covers it.
> 3. **Burden of proof is on the finding.** Confirm only when you can cite specific `file:line` proving the problem is real. Anything you can't prove — including uncertain — is `refuted`.
>
> Return a JSON array, one object per candidate, nothing else:
>
> ```json
> {
>   "id": "s1-c1",
>   "verdict": "confirmed" | "refuted",
>   "evidence": "file:line citations proving the verdict",
>   "corrected_severity": "P0" | "P1" | "P2",
>   "reason_if_refuted": "why it's a false positive, e.g. 'null filtered by guard at foo.ts:12'"
> }
> ```
>
> `evidence` is mandatory for `confirmed` and must contain ≥1 `file:line` you actually read. `corrected_severity` downgrades a real-but-minor candidate. Don't invent new findings — only judge the ones given.

After it returns: drop every `refuted`, keep `confirmed`, apply `corrected_severity`, attach `evidence`. Group by severity (P0 → P1 → P2), sort by file then line.

## Step 3.5 — Design judgment (advisory)

The find→verify pipeline only catches *falsifiable* problems. It can't judge the gestalt: **is this change the right shape at all?** That's taste, not a provable bug — the skeptic would refute it, the diff-only finders can't see the codebase — so it's owned by **you, the main context** (you already hold the diff, rules and confirmed findings).

After Step 3, do **one** holistic pass over the whole diff and ask a small, fixed set:

1. **Is the change needed at all?** — does it solve a real problem, or is it dead/speculative code, a config that defaults the same way, a guard for a case that can't happen?
2. **Does it add work that wasn't there?** — a request / render / effect / subscription the app didn't do before and doesn't need. (Concrete, line-pinnable instances belong to Finder 5 + verification; here you flag the *pattern* even when you can't pin one line.)
3. **Is there a categorically simpler path?** — not "this helper already exists" (Finder 3 owns that), but "this whole approach could be replaced by a simpler one" — derived state instead of synced state, a built-in instead of a hand-rolled loop, deleting code instead of adding a branch.

Rules for this pass:

- **Advisory, never a finding** — own tagged section, never in the P0/P1/P2 buckets.
- **At most 3; silence is valid** — don't manufacture notes to fill space.
- **Each note = one question to the author + one concrete alternative.** No vague "consider refactoring"; if you can't name the simpler path, you don't have a note.

## Step 4 — Output

Every finding has survived verification. Render with before/after snippets when present, and always include the skeptic's `_Проверено:_` line so the user can audit the verification.

````markdown
# Code Review — <N> confirmed findings
_Applied rules: <list>_ · _Missing: <list or "none">_
_Candidates: <total> · confirmed: <N> · refuted: <total − N>_

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
_Проверено:_ <evidence — the file:line the skeptic read that confirms this>.

## P1 — Should fix (<count>)
Same shape as P0.

## P2 — Nice to fix (<count>)
- **`file.ts:78`** — issue. _Fix:_ <one-liner> _(source: <rule_source>)_. _Проверено:_ <evidence>.

## Summary
<2-3 sentences: risk level, headline concerns, merge recommendation. Note the refuted count in one clause if any — it shows the review filtered rather than found nothing.>

## 💭 Design notes (advisory, unverified)
> Not bugs — questions about the change's intent (Step 3.5); you may be missing the author's context. Max 3, each a question + a concrete alternative. Omit the whole section when there are none.
- **Needed?** <one sentence + concrete alternative>
- **Added work:** <what it adds and why it isn't needed>
- **Simpler path:** <a categorically simpler approach>
````

Use the right language hint in fences (`ts`, `tsx`, `css`, `twig`, …). Omit empty buckets. If zero findings survive, output `Code Review — no confirmed issues in <N> files / <M> lines.` plus the candidate tally and manifest header.

After the report, when there is ≥1 confirmed P0/P1, end with one line offering to apply: `Применить подтверждённые P0/P1? (review-staged fix)`. Do not apply now — wait for the user.

## Step 5 — Apply (opt-in)

Triggered **only** by an explicit user request after the report: `review-staged fix`, or a reply like «исправь», «применить», «накати фиксы». Never apply in the same turn as the report, and never without this signal.

When triggered:

1. Apply **only confirmed** findings (those that survived Step 3), and only **P0/P1** — skip P2 unless the user names them.
2. For each, `Edit` the file using the skeptic-validated `snippet_before` → `snippet_after`. If `snippet_before` no longer matches the file (it drifted since the review), skip that finding and report it as stale rather than guessing.
3. **Surgical only** — change exactly what the finding describes. No adjacent cleanups, no reformatting, no renames «заодно».
4. After editing, list what changed (`file:line` per applied finding) and what was skipped (stale / P2 / refuted).
5. Honor git rules: do **NOT** `git add` / `git commit`, do **NOT** run `build`. Leave staging to the user.

## Hard constraints

- INLINE: no plan mode, no `ExitPlanMode`/`AskUserQuestion`, no other slash commands. **Read-only by default — no file writes during Steps 0–4.** Code changes happen only in the opt-in Step 5, after an explicit user request, and only on confirmed P0/P1.
- **Never show an unverified candidate as a finding** — only `confirmed` verdicts reach the report. The skeptic must actually inspect the real files (`Read` + `LSP`, `Grep` only to locate); re-reading the diff isn't verification. If the skeptic fails or returns invalid JSON: note it in the Summary and tag any shown candidates `⚠ UNVERIFIED` so the user knows none were confirmed.
- **Design notes (Step 3.5) are advisory** — the only unverified text in the report; never in the P0/P1/P2 buckets.
- Every finding's `file:line` must point at a diff-touched line; drop violators on merge.
- Cite rules by filename via `rule_source`; never restate rule files in the report.
- If a finder returns invalid JSON, note it in the Summary and continue with the rest.
