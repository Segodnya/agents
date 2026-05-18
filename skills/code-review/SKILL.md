---
name: code-review
description: Parallel code review of the staged diff with severity buckets and code-snippet fixes. Inline output only — no file writes, no plan mode, no clarifying questions. Use when the user asks for a code review, says "ревью", "review staged", or wants safety/architecture/style/integration/performance audit of pending changes.
---

# Code Review

Review the staged diff (`git diff --staged`) by fanning out work to 5 parallel subagents via the Task tool. Output INLINE in this response — no file writes, no plan mode, no clarifying questions first.

## Step 0 — Ground the review

Before spawning subagents, **discover** the rule sources for this review. Do not hard-code rules into the charters — the rule files are the source of truth.

1. Discover and load (in this order, skip missing):
   - `AGENTS.md` at the repo root, plus any nested `AGENTS.md` it references.
   - `CLAUDE.md` at the repo root, plus any nested `CLAUDE.md` it references.
   - All `~/.claude/rules/*.md` (user's global rules).
   - All `docs/rules/*.md` in the repo.
2. Build a **rule manifest** capturing which sources loaded and which were missing. Both are surfaced in the final report header so the user knows what informed the review.
3. Run `git diff --staged --stat` to see the scope. If the diff is empty, stop and report "no staged changes".

Pass the staged diff, the loaded rule files (verbatim), and the manifest to every subagent as context. Do **not** make subagents re-read rule files or re-derive rules from memory.

## Step 1 — Spawn 5 parallel subagents (single message, multiple Task tool calls)

Each subagent receives: the full staged diff, the rule files inline, the manifest, and its specific charter. Each subagent MUST return a JSON array of findings — nothing else.

**Common prelude (prepend to every subagent prompt):**

> Apply the loaded rule files as the **single source of truth** for style / architecture / naming / localization / integration / deployment norms. Do NOT re-derive rules from memory or training data. Flag a violation only if it is either (a) traceable to a specific line in the loaded rules, or (b) a universal correctness / security / performance principle independent of project style. When citing a rule, reference its file name so the user can verify.
>
> **Diff-scope only.** Review ONLY lines that appear in the staged diff (added or modified hunks). Do not flag adjacent code, pre-existing violations in the same file, or "while you're here" cleanups. If the diff doesn't touch a line, it's out of scope — even if it violates a rule. Exception: a diff-line that *depends on* broken adjacent code (e.g. new code calls into a buggy existing function the diff also touches) is fair game.
>
> **Trust the linter.** Do not flag anything the project's eslint / stylelint / tsc config already covers — assume CI catches it. If a rule has a corresponding lint rule, skip it. Focus on what the linter can't see or suggest which rule should team add to their config.

Finding schema:

```json
{
  "severity": "P0" | "P1" | "P2",
  "file": "path/to/file.ts",
  "line": 123,
  "issue": "one-sentence description of the problem",
  "rule_source": "architecture.md | code-style.md | universal | ...",
  "snippet_before": "exact code from the diff that has the problem",
  "snippet_after": "concrete fixed version of the snippet",
  "fix": "one-sentence rationale for the fix"
}
```

`snippet_before` / `snippet_after` are mandatory for P0 and P1 findings. For P2 they are optional (omit if a one-line rename or formatting tweak makes the fix obvious). `rule_source` is mandatory — use `"universal"` for principles not tied to a loaded rule file.

Severity guide:

- **P0** — bug, regression, security issue, data loss, type error, broken build, quadratic-or-worse on a path that can hit large n. Must fix before merge.
- **P1** — architecture violation, integration risk, missing edge-case handling, quadratic on bounded-but-growable input, rule violation that will cause pain. Should fix.
- **P2** — style, naming, duplication, micro-inefficiency, minor cleanup, drift signal. Nice to fix.

### Subagent 1 — Safety & Correctness

Owns: correctness and security primitives that the rule files don't usually cover.

- Logic bugs, null/undefined, off-by-one, race conditions, unhandled promise rejections, async/await misuse.
- Type errors and unsafe casts.
- Security: XSS, injection, secrets in code, unsafe `dangerouslySetInnerHTML`, unvalidated user input crossing trust boundaries.
- Broken contracts: changed return types without updating callers, breaking API changes.
- Edge cases: empty arrays, long strings without spaces, overflow, missing fallbacks.

### Subagent 2 — Architecture Compliance

Owns: layer / slice / module boundaries as defined in the loaded rules.

- Apply whatever architecture rules the loaded files (AGENTS.md, CLAUDE.md, `architecture.md`) prescribe — FSD layer direction, public-API discipline, mixins policy, mapper placement, view-class conventions, compound-component layout, `types/` and `constants/` policies, lifecycle ownership, etc.
- Flag any structural choice in the diff that contradicts a loaded rule. Cite the rule file in `rule_source`.
- Do not invent architecture rules that aren't in the loaded sources.

### Subagent 3 — Style, Naming & Duplication

Owns: style / naming concerns the linter can't catch, and duplication across slices.

- Apply whatever style / naming rules the loaded files (`code-style.md`, `naming.md`, project CLAUDE.md) prescribe — exports, return discipline, switch shape, async/await, destructuring placement, handler/callback/flag naming, props-interface naming, constants policy, etc.
- Cross-slice duplication: logic repeated in multiple places that belongs in a shared module.
- Magic numbers/strings only when repeated or semantically opaque per the loaded rules.
- Do not flag anything the project's eslint config already covers (assume CI catches lint).

### Subagent 4 — Integration & Deployment Risk

Owns: how this diff interacts with the rest of the system at runtime and at ship time.

- Localization: i18n called where keys are used; user-facing error messages understandable.
- Translation keys: new keys referenced but missing from locale files; removed keys still referenced.
- Query keys via functions (not raw strings) for cache invalidation.
- Forms: page preventer and confirm-modal for unsaved changes.
- Dynamic imports for anything that can reasonably be lazy.
- CSS-first for visual concerns; no JS where CSS suffices.
- `caniuse` check on new CSS/JS features (no polyfills).
- Components auto-destroyed via `_addComponent` lifecycle (or project equivalent).
- API methods bubbling vs swallowing errors per project convention.
- Deployment risk: feature flags, env vars, migration order, backwards compatibility of changed payloads.

(Performance / parallelism concerns — including `Promise.all` for parallelizable async — belong to Subagent 5.)

### Subagent 5 — Performance & Complexity

Owns: **strict algorithmic complexity** — clean-looking code that hides O(n²) or worse, plus avoidable sequential I/O. Inspired by the "O(n²) bug that looked like clean code" failure mode: nested array scans wrapped in `.filter` / `.find` / `.includes` read like prose but multiply at scale.

Signals to flag (each P0/P1 finding must include a one-line complexity callout, e.g. `O(n*m) → O(n+m)`):

- **Nested membership lookups** — `.find` / `.includes` / `.indexOf` / `.some` inside `.map` / `.filter` / `for` over the same or another collection. Fix: lift the inner collection into a `Set` or `Map` once outside the loop.
  ```ts
  // O(n*m)
  const enriched = users.map((u) => ({ ...u, role: roles.find((r) => r.id === u.roleId) }));
  // O(n+m)
  const roleById = new Map(roles.map((r) => [r.id, r]));
  const enriched = users.map((u) => ({ ...u, role: roleById.get(u.roleId) }));
  ```
- **Chained passes** — `.filter().map().find()` over a large array where a single pass or early exit would do.
- **`.sort()` inside render or on every event** — sort once, memoize, or sort on write.
- **Repeated heavy construction inside a loop** — `new RegExp(...)`, `JSON.parse(JSON.stringify(...))`, `new Date(...)` parsed from a constant. Hoist outside.
- **Sequential `await` in a loop** when iterations are independent → use `Promise.all` / `Promise.allSettled`. (Moved here from integration concerns — it is a complexity / latency multiplier.)
- **N+1** API or DB calls — one fetch per item where one batched fetch exists.
- **Recursive set/tree building without memoization** when input size is non-trivial.
- **Accidentally quadratic string building** — repeated `str += ...` over large n where chunks + `join` would be linear in practice.

Severity:
- **P0** — quadratic-or-worse on a path that can plausibly see large n in prod (user data, paginated lists, search results).
- **P1** — quadratic on bounded-but-growable input, or sequential awaits that should parallelize.
- **P2** — micro-inefficiency on tiny / fixed-size inputs (call out as drift, not blocker).

Each P0/P1 finding's `snippet_after` shows the Set/Map (or `Promise.all`) fix; `fix` line names the complexity delta.

## Step 2 — Merge findings

After all 5 subagents return, parse their JSON arrays and merge:

1. **Deduplicate** by `(file, line, issue similarity)`. If two subagents flag overlapping issues, keep the higher severity and combine the fix suggestions (prefer the more concrete snippet). Prefer the finding with a specific `rule_source` over a generic one.
2. **Group by severity**: P0 → P1 → P2.
3. **Sort within each group** by file path, then line.

## Step 3 — Output inline

Produce a single markdown report directly in the response (NOT a file). Render each finding with before/after code snippets when present:

````markdown
# Code Review — <N> findings
_Applied rules: <comma-separated list from manifest>_
_Missing: <comma-separated list, or "none">_

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

_Why:_ <fix rationale> _(source: <rule_source>)_.

## P1 — Should fix (<count>)

### `file.ts:45` — issue description

```ts
// before
<snippet_before>
```

```ts
// after
<snippet_after>
```

_Why:_ <fix rationale> _(source: <rule_source>)_.

## P2 — Nice to fix (<count>)

- **`file.ts:78`** — issue description. _Fix:_ <one-line fix> _(source: <rule_source>)_.

## Summary
<2-3 sentences: overall risk level, headline concerns, merge recommendation.>
````

Use the appropriate language hint in fences (`ts`, `tsx`, `js`, `css`, `twig`, etc.). If a severity bucket is empty, omit its section. If there are zero findings, output: `Code Review — no issues found in <N> files / <M> lines.` (still include the manifest header so the user knows which rules were applied).

## Hard constraints

- INLINE OUTPUT ONLY. Do not write to any file. Do not enter plan mode. Do not call ExitPlanMode or AskUserQuestion.
- Do not modify any code in the repo — review only.
- Do not invoke other slash commands.
- Do not restate or paraphrase the loaded rule files inside the report. Cite them by filename via `rule_source`.
- If a subagent fails or returns invalid JSON, note it in the Summary and continue with the rest.
- Cap each subagent at 25 findings; if it hits the cap, surface that in the Summary.
- For tiny diffs (≤3 files OR <100 lines changed), skip the fan-out and do a single inline pass covering all **5 charters** yourself — still produce the same severity-bucketed output with snippets and the manifest header.
