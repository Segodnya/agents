---
name: graph-workflow
description: Graph of bounded nodes for a large task — a subagent per node, a gate on every output, code for deterministic steps, run files another session resumes from. Use when the user asks to run work as a graph or split it into nodes, or to resume a graph run.
disable-model-invocation: true
---

# Graph workflow

Run the task as a **graph**: bounded **nodes** joined by **edges**, every node output passed through a **gate**. Work like autoresearch: take a **baseline** first, change one thing per attempt, keep what measures better, and log what failed.

## Run folder

All run files live in one run folder, by default `graph/` beside the deliverable.

- `graph.md`: assumptions, nodes, edges, code nodes, learned rules.
- `checks.md`: the **ledger**, one row per requirement: `node | requirement | verdict | evidence`. The verdict is pass, fail or unresolved. The evidence is a file path or literal check output; a row without evidence stays unresolved.
- `progress.md`: nodes done, running and blocked; current output paths; decisions that changed `graph.md` and why; open issues; the next ready node.

**Resume:** if `progress.md` exists, read `graph.md` and `progress.md`, then go to step 4 at the recorded next node.

## Steps

1. **Pre-flight.** Read six inputs from the invocation and the conversation: task, deliverable with output path, acceptance checks, sources, scope (paths nodes may write), run limit. Ask for every missing or vague one in one numbered list; give each a one-line reason (scope sets where nodes may write, checks set what gates test). If the user says to make assumptions, record each under `## Assumptions` in `graph.md`; an unclear scope means read-only.
   Done when every input except sources has a user value or a recorded assumption.
2. **Baseline.** Run every acceptance check against the current state. Record each result in the ledger with node `baseline`.
   Done when every acceptance check has a baseline row.
3. **Split.** Write nodes, edges and code nodes into `graph.md` by the node rules below. Write the completion checklist from the acceptance checks; each item names its observable evidence. Check the active permissions against the scope (see Permissions). Show the graph and the checklist, then stop for approval.
   Done when the user approves.
4. **Execute.** Repeat until the checklist is complete, a blocker stops all ready nodes, or the run limit is reached:
   1. Pick the ready nodes: every input exists.
   2. Dispatch independent nodes in parallel in one message. Run code nodes as scripts.
   3. Gate each output against its saved file.
   4. Send a failed output down its correction edge. Merge a passed output with code.
   5. Update the ledger and `progress.md`.
5. **Deliver.** Return the paths to the deliverable, `graph.md`, `checks.md` and `progress.md`. Report what passed, what failed and what is unresolved, each with its evidence. On a limit or a blocker, list the exact nodes left. Write learned rules into `graph.md`.
   Done when `graph.md` lists exactly the nodes that ran and every checklist item has a ledger row.

## Nodes

- A node is one bounded job: one input, one output, one check.
- Split by what each node must read, not by folder. Prefer fewer nodes when results are comparable.
- Draw an edge only when a node consumes another node's output. Nodes with no path between them run in parallel.
- A step with exactly one correct answer (merge, sort, dedupe, compare, count) is a **code node**: a script listed in `graph.md`. Subagents get judgment work only.
- Each file has one writer node.
- A subagent prompt carries the node spec, its input paths, its check and its write scope. The subagent returns the output path, the verdict, and the evidence as command plus output.

## Gates

- Weigh evidence in this order: deterministic checks, the node's report, model confidence.
- A gate's check stays fixed for the whole run. A check that proves wrong goes to the user as an open issue; the gate keeps its original check until the user changes it.

## Return paths

- **Correction edge:** a failed gate sends the output back to the node that made it, with the reason, the evidence and the scope "fix this node only". After three failed attempts, mark the row unresolved and continue with the nodes that do not depend on it.
- **Learning edge:** an accepted result writes the rule it proves under `## Learned rules` in `graph.md`, so the next run starts from it.

## Permissions

- Keep writes inside the scope. A prompt is not a boundary: where a stray write is costly, enforce the scope with permission rules or a sandbox.
- Ask the user before publishing, sending or spending.
- Before a risky merge, copy the affected files into `backup/` in the run folder.
- If a required tool is missing, record the blocker in `progress.md` and finish the nodes that do not need it.
