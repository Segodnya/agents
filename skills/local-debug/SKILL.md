---
name: local-debug
description: Interactive local debugging loop — collect a bug description, investigate the codebase, instrument suspected hot spots with console.warn probes, wait for the user to reproduce and paste logs, then diagnose root cause and propose the minimal architectural fix. Use when the user says "/local-debug", "debug this bug", "help me debug", "let's debug locally", "добавь логов", "локальный дебаг", or describes a runtime/UI bug they want traced live in the browser or Node.
---

# Local Debug

A four-step interactive loop. Do NOT skip steps. Do NOT propose a fix before logs come back.

## Step 1 — Collect bug info

Use AskUserQuestion to gather what you need. Ask in ONE call, up to 4 questions, only what is not already provided:

1. **Bug** — what is the user-observable wrong behavior? (skip if already stated)
2. **Repro** — exact steps to reproduce (URL/route, inputs, clicks).
3. **Expected vs actual** — what should happen vs what happens.
4. **Surface** — which area/module/component/page? (skip if obvious)

If the user already gave a full description in their initial message, skip the question and confirm understanding in one sentence.

## Step 2 — Investigate the codebase

Trace the data flow. Don't guess — read.

- Locate the entry point (component, route, handler, action) tied to the repro.
- Walk the call graph: component → hook → store/query → api/mapper → server response. Or: event → handler → reducer → selector → render.
- Identify the **boundaries** where the bug could live:
  - data shape mismatch (API contract drift, mapper bug)
  - stale cache layer (RTK Query, react-query, memoized selector)
  - provider boundary (context value identity, missing provider)
  - render order / effect timing (useEffect deps, race, double-render)
  - state shape (normalization, reference equality)
  - async race (concurrent requests, abort, stale closure)
- Use Grep/Read aggressively. Prefer the Explore agent if the surface is broad (≥3 layers to trace).

Report a short trace back to the user (3–6 bullets) naming the suspected mechanism(s) BEFORE instrumenting.

## Step 3 — Instrument with console.warn probes

Add temporary `console.warn` calls at every boundary that matters for the hypothesis. Rules:

- **Prefix every log** with a unique tag so the user can grep: `console.warn('[local-debug:<slug>]', label, value)` where `<slug>` is a short kebab name derived from the bug (e.g. `[local-debug:cart-total]`).
- **Log shapes, not just values**: for objects, log the object directly (browser devtools renders it live). For primitives, log with a label.
- **Cover the boundary, not the leaf**: log inputs AND outputs of the suspect function, both sides of a mapper, before AND after a setState/dispatch.
- **Time-sensitive paths** — include `performance.now()` or a counter to spot ordering/races.
- **Don't reformat surrounding code.** Surgical inserts only. No refactors, no renames, no "while I'm here" cleanup.
- **Don't gate behind env checks.** These are temporary — they will be removed in Step 4 once the cause is found.
- **List every file you touched** at the end of the message with the tag you used, so removal is trivial (`rg "\[local-debug:<slug>\]"`).

After inserting, tell the user **exactly what to do**:

> Reproduce the bug now. Open devtools console, filter by `[local-debug:<slug>]`, then paste the log output back here. If logs are empty or a probe didn't fire, tell me which one — that itself is a clue.

Then STOP and wait. Do not propose a fix yet.

## Step 4 — Diagnose and propose fix (after user pastes logs)

When logs arrive, follow this template literally — it is the contract for this skill:

> **Bug:** <one-line restatement>
>
> Before proposing a fix, trace the data flow and identify the exact mechanism (data shape mismatch, stale cache layer, provider boundary, render order). State the root cause first, then propose the minimal architectural fix — not a local workaround.

Then produce:

1. **Data flow** — 3–6 numbered lines showing what the logs prove happened, in order. Reference probe tags.
2. **Root cause** — one paragraph naming the exact mechanism. Be specific: "the mapper drops `nullable` fields when the upstream payload uses `null` instead of omitting the key" beats "data shape issue".
3. **Why the obvious fix is wrong** — one or two sentences ruling out the local workaround (e.g. "patching the component to coalesce undefined hides the same bug for every other consumer of this selector").
4. **Minimal architectural fix** — name the file(s) and the change. Prefer the fix that lives at the boundary where the contract is violated. Show a small before/after snippet.
5. **Probe cleanup** — list the files + tag to remove, and offer: "Want me to remove the probes now?" (wait for confirmation before deleting).

## Hard constraints

- Never propose the fix in the same turn you add probes. The loop has two halves separated by the user's repro.
- Never add probes without first reporting the trace from Step 2 — the user needs to sanity-check the hypothesis before running code.
- Never log secrets, tokens, auth headers, or full user PII. If a payload may contain them, log the shape (`Object.keys(...)`) or a redacted subset.
- Never edit code outside the probe inserts. No refactors. No formatting fixes. No comment cleanup.
- Never gate probes behind a flag — they are temporary and must be removed in Step 4.
- If the bug is clearly a one-liner (typo, wrong variable name visible in a 10-line function), skip Step 3 entirely — say so and jump to Step 4 with the fix. The loop is for non-obvious bugs.
- Honor the user's git rules: do NOT `git add` or `git commit` the probe inserts.
