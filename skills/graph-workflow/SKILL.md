---
name: graph-workflow
description: Graph of bounded nodes for a large task — subagents for heavy nodes, a gate on every output, code for deterministic steps, run files another session resumes from. Use when the user asks to run work as a graph or split it into nodes, or to resume a graph run.
disable-model-invocation: true
---

# Graph workflow

Run the task as a **graph**: bounded **nodes** joined by **edges**, every node output passed through a **gate**. Work like autoresearch: take a **baseline** first, change one thing per attempt, keep what measures better, and log what failed.

## Run folder

All run files live in one run folder, by default `graph-<ticket key>/` beside the deliverable, or `graph-<task slug>/` without a ticket. Each task gets its own folder.

- `spec.md`: the task, `## Design` (each decision with its reason), `## Checklist` (the completion checklist). It is the user's contract (see Gates).
- `graph.md`: assumptions, nodes, edges, code nodes, learned rules.
- `checks.md`: the **ledger**, one row per requirement: `node | requirement | verdict | evidence`. The verdict is pass, fail or unresolved. The evidence is a file path or literal check output; a row without evidence stays unresolved.
- `progress.md`: nodes done, running and blocked; current output paths; decisions that changed `graph.md` and why; open issues; the next ready node.

**Hand-off:** a phase ends where the next phase needs only the run files and the diff. There, bring `progress.md` up to date and tell the user to run `/clear` before the next phase.

## Steps

0. **Run folder.** List the run folder before the first write. On a resume request, read `spec.md`, `graph.md` and `progress.md`, then go to step 4 at the recorded next node. Otherwise stop at a `progress.md` or any file from another task: resume that run or pick a new folder.
   Done when the folder is empty or its `progress.md` has been read.
1. **Pre-flight.** Dispatch a `recon` subagent for the reading: ticket, attachments, Figma, code. Its report has a fixed format: a draft of the six inputs, candidate nodes with `file:line`, and open questions, each option measured on the real system. The six inputs are task, deliverable with output path, acceptance checks, sources, scope (paths nodes may write), run limit. Fill them from the invocation, the conversation and the report. Ask for every missing or vague one in one numbered list; give each a one-line reason (scope sets where nodes may write, checks set what gates test). When the task itself is **underspecified** (a behaviour no source describes), **ambiguous** (two readings lead to different code) or **contradictory** (two sources disagree), invoke `Skill mattpocock-skills:grilling` instead of the list; the missing inputs join its first round. Write every decision the user settles, in the list or in grilling, with its reason, under `## Design` in `spec.md`. If the user says to make assumptions, record each under `## Assumptions` in `graph.md`; an unclear scope means read-only.
   Offer an option only with its measured number: the question states what changes and the number that shows it.
   Done when every input except sources has a user value or a recorded assumption, and every settled decision is in `spec.md`.
2. **Baseline.** Run every acceptance check against the current state. Record each result in the ledger with node `baseline`.
   Done when every acceptance check has a baseline row.
3. **Split.** Write nodes, edges and code nodes into `graph.md` by the node rules below. Write the completion checklist from the acceptance checks into `spec.md`; each item names its observable evidence. Check the active permissions against the scope (see Permissions). Show the graph and the checklist, then stop for approval. After approval, write `progress.md` with the next ready node and hand off (see Run folder).
   Done when the user approves and `progress.md` names the next ready node.
4. **Execute.** Repeat until the checklist is complete, a blocker stops all ready nodes, or the run limit is reached:
   1. Pick the ready nodes: every input exists.
   2. Dispatch independent subagent nodes in parallel in one message. Run code nodes as scripts.
   3. Gate each output against its saved file.
   4. Send a failed output down its correction edge. Merge a passed output with code.
   5. Update the ledger and `progress.md`.
5. **Review.** Skip when the nodes wrote no files in a git repo. Otherwise run rounds `r1` to `r3`:
   1. Stage the files the nodes wrote: `git add -- <paths>`, the paths taken from the writer nodes in `graph.md`, the run folder excluded. Before `r1`, `git diff --staged --name-only` names a file outside that list → ask the user: the staged review would include it.
   2. Dispatch a `review` subagent: «Invoke `Skill segodnya-agents:review-staged` with `staged --checklist <run>/spec.md --rs-dir /tmp/<run>/review-r<n>` and `--mr <url>` if the inputs name an MR, else `--no-mr`. Run the whole procedure, four axes and the gate. Ask nothing: write `MISSING: <what>`. Reply with only the report path, one line per open entry of `gate.json` (not `✅ снято тредом`, not `↩︎ отменяет правку`) `<id> · file:line · P? · kind · claim`, and `open: <number>`.» No `gate.json` in the `--rs-dir` → one `SendMessage` to the same reviewer.
   3. Write each finding as a ledger row with node `review-r<n>`. Group the findings by the node that wrote their file and send each group down that node's correction edge, all groups in one message; a review fix counts toward the node's three attempts. Gate each fixed output as in step 4. A finding of `kind: unpromised`, or one that disputes `## Design`, is an open issue for the user.
   Done when a round returns no open finding except the user's open issues, or after `r3`; the findings still open stay unresolved.
6. **Deliver.** Return the paths to the deliverable, `spec.md`, `graph.md`, `checks.md` and `progress.md`. Report what passed, what failed and what is unresolved, each with its evidence. On a limit or a blocker, list the exact nodes left. Write learned rules along the learning edge. Hand off to the follow-up skills: commit message, deploy checklist, `/simplify`, retro.
   Done when `graph.md` lists exactly the nodes that ran and every checklist item has a ledger row.

## Nodes

- A node is one bounded job: one input, one output, one check.
- Split by what each node must read, not by folder. Prefer fewer nodes when results are comparable.
- Draw an edge only when a node consumes another node's output. Nodes are independent when no path joins them and they share no exclusive resource.
- A step with exactly one correct answer (merge, sort, dedupe, compare, count) is a **code node**: a script listed in `graph.md`.
- A script that runs twice is a file in the run folder, called by path: `python3 <file>`, `evaluate_script` with `sourcePath`.
- Pick the executor by cost and write it next to each node in `graph.md`. A node whose reading or iteration the main session will not need again runs in a subagent; its reading and reasoning stay there. A cheap node that needs the main session's context, such as an edit at known lines of one file, runs in the main session.
- **Browser node:** a subagent that owns the chrome-devtools browser; subagents reach the MCP browser like the main session. It runs the browser checks of the baseline and every browser gate, and returns ledger rows `check | value | screenshot path`.
- Each file has one writer node. Any other exclusive resource (the browser, a dev-server port) has one owner node at a time.
- A subagent prompt carries the node spec, its input paths, its check, its write scope and a scratch folder `/tmp/<run>/<node>/` for downloads, packed compilers and tree copies; the run folder keeps only results. The subagent returns the output path, the verdict, and the evidence as command plus output.

## Gates

- Weigh evidence in this order: deterministic checks, the node's report, model confidence.
- A resource check (peak memory, wall time) compares medians of at least 3 runs per side; one run against one run measures noise.
- A gate's check and `spec.md` stay fixed for the whole run. A check that proves wrong goes to the user as an open issue; only the user changes them.

## Return paths

- **Correction edge:** a failed gate sends the output back to the node that made it, with the reason, the evidence and the scope "fix this node only". After three failed attempts, mark the row unresolved and continue with the nodes that do not depend on it.
- **Learning edge:** an accepted result writes the rule it proves into the rule file that already covers its area (the repo's agent rules, or a doc an always-loaded pointer reaches), so the next run starts from it. `## Learned rules` in `graph.md` keeps only rules about this run.

## Permissions

- Keep writes inside the scope. A prompt is not a boundary: where a stray write is costly, enforce the scope with permission rules or a sandbox.
- Ask the user before publishing, sending or spending.
- Before a risky merge, copy the affected files into `backup/` in the run folder.
- If a required tool is missing, record the blocker in `progress.md` and finish the nodes that do not need it.
