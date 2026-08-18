---
name: self-local-debug
description: Interactive local debugging loop — collect a bug description, investigate the codebase, instrument suspected hot spots with console.warn probes, wait for the user to reproduce and paste logs, then diagnose root cause and propose the minimal architectural fix. Includes a prod-build track (pasteable console snippets, snapshot and arm-before-load) for bugs that do not reproduce on the local build. Use when the user says "/self-local-debug", "debug this bug", "help me debug", "let's debug locally", "добавь логов", "локальный дебаг", "не воспроизводится на локальной сборке", "только на билде", or describes a runtime/UI bug they want traced live in the browser or Node.
---

# Self Local Debug

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
  - **build-only**: chunk/CSS load order, minifier semantics, `NODE_ENV` branches, sourceless third-party bundle — these cannot reproduce on the dev build at all (see Step 3b)
- Walk the call graph via `LSP` (ts/js/tsx, php, rust, go): `outgoingCalls`/`goToDefinition` to follow the flow forward, `findReferences`/`incomingCalls` for callers, `hover` for the real type at a boundary. Grep only to locate a symbol's position; `Read` for surrounding context. Prefer the Explore agent if the surface is broad (≥3 layers to trace) — give it `LSP` access.

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

## Step 3b — Prod-build probes (bug does not reproduce locally)

**Trigger — switch to this track the moment any of these is true:**

- the user says «не воспроизводится на локальной сборке», «только на билде / на проде»;
- Step 3 logs come back **healthy** while the bug is visibly there — a clean log from a build that doesn't have the bug is not evidence, the probes simply never entered the broken scenario;
- Step 2 named a build-only mechanism (chunk order, minifier, `NODE_ENV`).

Say this out loud and stop re-instrumenting the dev build. A second dev pass is a guaranteed blank run — it costs the user a full repro and proves nothing.

You cannot edit minified prod code, so the probe becomes a **self-contained console snippet the user pastes into the broken page**.

### Delivering the snippet

- Write it to **one file in the work tree**: `.tmp-shots/<slug>-probe.js`. Hand over a ready command, not the code in chat:
  `cat /abs/path/.tmp-shots/<slug>-probe.js | pbcopy`
  (chat round-trips mangle quotes, and the user re-copying 100 lines by hand is a wasted turn).
- **Verify it parses before delivering**: `node -e "new Function(require('fs').readFileSync('<path>','utf8'))"`. A syntax error burns an entire repro run.
- **ES5-safe, no build**: `var`, `function`, no optional chaining, no JSX, no imports. It runs against the prod bundle in whatever browser the user has open.
- **No app internals by name** — minified prod has no module names and no `require`. Reach only through DOM (`querySelector`, `getBoundingClientRect`, `getComputedStyle`, `document.styleSheets`), globals, and native prototypes.
- **`copy()`, don't `console.log`.** Objects logged to console arrive back collapsed as `{…}` and are useless. End with `copy(JSON.stringify(out, null, 1))`.
- **Accumulate, never overwrite.** A second `copy()` silently clobbers the first — that is how the "broken" snapshot gets lost the moment the user captures the "ok" one. Push into `window.__snaps` and expose a separate dump function that copies everything at once.

### Two snippet shapes

**A. Snapshot — `__snap(label)` / `__dump()`.** For state readable after the fact: geometry, computed styles, inline styles, class names, CSS variables, stylesheet count/order/owner node. Ask for at least two labelled snapshots — `broken` and `ok` (after whatever workaround fixes it). The diff between them is the finding; e.g. `sheets: 147` broken vs `150` ok says three stylesheets landed after render.

**B. Arm-before-load — `__armReport()`.** For anything time-dependent: races, ordering, a value already overwritten by the time anyone can look.

- Patch the suspect property or method: `Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop')` + `defineProperty`, and log `{t, requested, actual, state before/after, new Error().stack.split('\n').slice(1,8)}`. This is what proves the browser silently clamped an assignment — `requested: 139.6, actual: 0` is a fact no dev-build log can give you.
- Run a `requestAnimationFrame` loop pushing one record per frame, **deduped against the previous record** — the log must be a state-change history, not 10 000 identical frames.
- Expose `__armReport()` that stops the loop and `copy()`-es `{scrollLog, frames}`.
- Guard re-entry: `if (window.__armed) return 'already armed';`

Arming survives nothing — a reload wipes every `window.__*`. Spell out the order literally:

> 1. Reload. 2. **Immediately** paste the snippet and Enter — it should answer `ARMED`. 3. Wait until the page settles in the broken state — **don't touch anything**: no resize, no scroll, no navigation. 4. Run `__armReport()` and paste the result here.

Step 3 is exactly what makes or breaks the run: resizing or scrolling re-triggers the recalculation whose absence is often the bug itself.

### Repro conditions to state explicitly

Build (not dev server), network throttling (`Fast 4G`), `Disable cache`, cold load. Name them every time — the user reproducing on a warm cache produces a healthy log.

Also ask **one discriminating question in words** per round (e.g. "ломается только под троттлингом или на обычной скорости тоже?"). It splits hypotheses that no snapshot can.

Then STOP and wait, same as Step 3.

## Step 4 — Diagnose and propose fix (after user pastes logs)

When logs arrive, follow this template literally — it is the contract for this skill:

> **Bug:** <one-line restatement>
>
> Before proposing a fix, trace the data flow and identify the exact mechanism (data shape mismatch, stale cache layer, provider boundary, render order). State the root cause first, then propose the minimal architectural fix — not a local workaround.

Then produce:

1. **Data flow** — 3–6 numbered lines showing what the logs prove happened, in order. Reference probe tags, timestamps and the actual numbers; make the arithmetic check out (`139.667 × 3 = 419` beats "the measurement was wrong").
2. **Root cause** — one paragraph naming the exact mechanism. Be specific: "the mapper drops `nullable` fields when the upstream payload uses `null` instead of omitting the key" beats "data shape issue".
3. **Why the obvious fix is wrong** — one or two sentences ruling out the local workaround (e.g. "patching the component to coalesce undefined hides the same bug for every other consumer of this selector").
4. **Minimal architectural fix** — name the file(s) and the change. Prefer the fix that lives at the boundary where the contract is violated. Show a small before/after snippet. If two changes are only correct together (a missing trigger AND a guard that burned a one-shot flag), say so explicitly.
5. **Probe cleanup** — list the files + tag to remove, plus any `.tmp-shots/` snippets, and offer: "Want me to remove the probes now?" (wait for confirmation before deleting).

**If the fix came out of the Step 3b track, add one more line, honestly:** the fix is **unverified against the real bug** — the local stand cannot reproduce it. Name what you *did* verify (`tsc --noEmit`, `eslint`, healthy path unchanged on the stand, no feedback loop from a new observer), and hand verification back with the exact scenario to run (build + throttling + disable cache + what must be visible).

## Hard constraints

- Never propose the fix in the same turn you add probes. The loop has two halves separated by the user's repro.
- Never add probes without first reporting the trace from Step 2 — the user needs to sanity-check the hypothesis before running code.
- Never re-instrument the dev build after it came back clean on a bug that's real on prod. Switch to Step 3b instead.
- Never log secrets, tokens, auth headers, or full user PII. If a payload may contain them, log the shape (`Object.keys(...)`) or a redacted subset.
- Never edit code outside the probe inserts. No refactors. No formatting fixes. No comment cleanup.
- Never gate probes behind a flag — they are temporary and must be removed in Step 4.
- Console snippets go to `.tmp-shots/` inside the work tree, never `/tmp` — and they are your garbage: list them and offer removal at the end.
- If the bug is clearly a one-liner (typo, wrong variable name visible in a 10-line function), skip Step 3 entirely — say so and jump to Step 4 with the fix. The loop is for non-obvious bugs.
- Honor the user's git rules: do NOT `git add` or `git commit` the probe inserts.
