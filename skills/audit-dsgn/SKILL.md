---
name: audit-dsgn
description: Frontend design audit of a Figma mockup against the codebase. Ends with a list of questions for the designer or product owner, and can publish them as pinned Figma comments. Use when the user gives a FIRSTAUDIT / DSGN ticket or a Figma link and asks for «аудит дизайна», «аудит макетов», «design review» or «вопросы дизайнеру», or asks to post audit questions as comments on the mockup.
---

The goal is a short list of **delta** questions, filtered by the gates in step 5.

## 1. Gather inputs

- Ticket: `jira issue view <KEY> --plain`. The description holds the designer checklist (designer, business owner, problem, use cases, Figma link) and the linked DSGN ticket.
- Figma URL: `fileKey` + `node-id` (convert `-` to `:`).
- Audit checklist: [CHECKLIST.md](CHECKLIST.md). If the request names a newer PDF, read it with `markitdown` and update CHECKLIST.md.
- Repo path: the one named in the request, otherwise the working directory.

Done when you can state the feature in one sentence and list its use cases from the ticket.

## 2. Launch code recon (background, parallel with step 3)

Two `Explore` agents, `model: sonnet`, `effort: medium`, in one message:

- **Host**: where the feature lands. Its files and framework, and how sibling items behave there, e.g. how they are configured, validated and saved (which click, which endpoint, which payload), how errors show, and how tariff and rights gates work.
- **Domain**: the data the feature touches and its constraints, e.g. API and payload, limits (value, enforcement, error code, UI text), statuses, permissions, delete cascades, and list endpoints for selects (page size, search, count).

Each prompt carries the feature sentence and use cases, plus the rule "every finding = file:line + the command you ran + its conclusion, mark the rest *not found*", plus the **NAV** block below.

**NAV** (into every agent prompt): `grep`/`rg` via Bash by default — one symbol, known file, "where is it declared" → grep (no `Grep`/`Glob` tool here; quote globs for zsh: `grep --include='*.ts'`, `rg -g '*.ts'`). LSP (ts/js/tsx, php, rust, go; `ToolSearch("select:LSP")` first) costs three round trips — only where grep answers wrong: consumer list (`findReferences` / `incomingCalls`), type of an expression, not a declaration (`hover`), what a method calls (`outgoingCalls`). Position from `workspaceSymbol`, always with `query`. Twig, CSS, `.po`, vendor bundles, untyped Backbone have no LSP coverage — grep and read the source.

## 3. Read the mockup

1. `get_metadata` on the node. A big section overflows to a file. In one `python3 -I` call, parse the JSON, write the XML to a temp file, and print the tree to depth 2 (id, name, x, y, w, h).
2. Annotation instances are leaves without text in metadata. Read them from screenshots.
3. `get_screenshot` returns a URL. Batch it:
   1. Issue all `get_screenshot` calls in one message.
   2. Download all files in one Bash call (`curl -sL -o … &` … `wait`) into `./.tmp-shots/audit-<KEY>/`.
   3. `Read` the images in one batch.

   A typical `maxDimension` is 1280 for screens, 800 for annotations and components, and 2400 for sections. Note the scale `original / rendered`: you need it to turn pixel points into Figma offsets.
4. Keep a **frame map**: node id → what the frame shows, plus annotation text near it.

Done when every top-level child of the section (screens, sections, annotations, loose components) is in the frame map. Each entry has its states and annotations read.

## 4. Find candidate issues

Walk the flow and every section against CHECKLIST.md. Look for inconsistencies between screens: wording, a control present on one screen and absent on another, annotations that contradict screens. Note the designer's own open questions in annotations.

## 5. Filter to delta questions

When the recon reports arrive, spot-check the claims your questions depend on with `grep`/`sed`. Then keep a candidate only if it passes all three gates:

1. **In scope** of this ticket. A misleading ticket title does not widen the scope.
2. **Not settled by existing behavior.** Look up how the closest sibling feature behaves in code (save timing, generic error handling, keyboard support of the reused control). If the feature inherits that behavior, drop the question or reword it as "confirm we keep X, as sibling Y does".
3. **Needs a human decision.** If only code can answer it, move it to the implementation notes.

Done when every kept question names the frame it is about and the decision it asks for.

## 6. Report

- A verdict on feasibility in one line, then an ASCII diagram of the data flow (UI → endpoints → entity).
- Code findings: what exists to reuse and what is missing (backend and frontend), with `file:line`.
- Questions, numbered, grouped: logic and data / rights and tariffs / states / content and consistency.
- Implementation notes, if any.
- Where the screenshots lie.

## 7. Publish as Figma comments (only when the user asks)

Follow [FIGMA-COMMENTS.md](FIGMA-COMMENTS.md).
