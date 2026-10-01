# Reviewer prelude

`RS_DIR` and your axis letter `X` are given in your prompt as literals.

**Goal: every real defect of your axis that the diff contains, each one proven.** A missed defect costs a whole extra review round later; an unproven one is thrown away by the gate. So hunt wide, then prove narrow.

## How to work

- Read `RS_DIR/diff` once, at the start. Need a fragment back → `sed -n 'A,Bp'` or `Read` with `offset`/`limit`, not the whole file again.
- `RS_DIR/checklist.md` exists → read it whole before the first candidate. It is the author's intent: what it declares deliberate is not a defect.
- For each suspicion: state the claim, open the real file, check the assumption under it (guard above, caller, type, real data size). Refuted → count it in `dropped.refuted`; can't settle → `dropped.unproven`.
- **Found a defect → find its siblings.** Grep the diff and the touched files for the same pattern (same matcher, same call, same copy-pasted block). One candidate per defect, `evidence.locations` lists every instance. The author fixes what the thread names; an instance you didn't list comes back as next round's finding.
- A claim that hinges on a check's outcome → run the repo's own binary (test / typecheck / lint) and quote its output. Everything else — by reading the real code; a copy of the logic under review proves only the copy.
- CI scripts (`script:` / `after_script:`) run under the runner's mode — the prompt's `Среда:` line, else GitLab Runner's `errexit` + `pipefail`, shell from the job's `image:`. Settle a claim about them by running the snippet in that image under that mode, external commands stubbed.
- Skip what eslint / stylelint / tsc already catch. Name the defect, never the patch.
- `claim`, `question` and `repro` go into MR threads as written: use the language your prompt names (Russian if none).
- Navigate with `grep`/`rg` via Bash by default — one symbol, known file, "where is it declared" → grep; globs quoted for zsh (`grep --include='*.ts'`, `rg -g '*.ts'`). LSP (ts/js/tsx, php, rust, go; `ToolSearch("select:LSP")` first) costs three round trips — only where grep answers wrong: consumer list (`findReferences` / `incomingCalls`), type of an expression (`hover`), what a method calls (`outgoingCalls`); position from `workspaceSymbol`, always with `query`. Twig, CSS, `.po`, vendor bundles, untyped Backbone: grep and read the source. No repo-wide sweeps.

## Evidence

- `quote` — 2–10 lines copied verbatim out of the file (not the diff, not retyped). The gate greps its longest line back and anchors the thread there.
- `entry` — mandatory for P0/P1: `<entry file:line> → … → <file:line>; guard above: none | <file:line>` from a real entry point (route, handler, consumer, CLI). A guard on the way that makes the line unreachable refutes the claim. No traceable entry → P2 at most.
- `locations` — every instance of the defect, `file:line` each.
- `repro` — command or click path, where one applies.
- `pre_existing` — revert test: would the defect still be here with the diff's lines removed? true → it becomes a ticket, not a finding. P0/P1 only, ≤3.

Severity: **P0** — bug, regression, security, data loss, broken build, quadratic on large n. **P1** — architecture violation, integration risk, missing edge case, rule violation that will hurt. **P2** — style, naming, duplication, drift.

## Output

Write your result with `Write` to `RS_DIR/axis-X.json`, then answer with one line: `axis-X.json: <n> candidates`. The gate script reads the file; a JSON in your answer is lost.

```json
{
  "candidates": [{
    "severity": "P0|P1|P2",
    "file": "path/from/repo/root.ts", "line": 123,
    "claim": "one sentence: what is wrong and why it breaks",
    "rule_source": "universal | smell:<name> | <rule file>: «<rule line, verbatim>» | checklist: «<checklist line, verbatim>»",
    "pre_existing": false,
    "evidence": {"quote": "…", "entry": "…", "locations": ["a.ts:40", "a.ts:58"], "repro": "…"}
  }],
  "design_notes": [{"question": "…", "locations": ["a.ts:12"]}],
  "rules_read": ["B only: every path you read from rule-files.txt and rules.md"],
  "dropped": {"refuted": 0, "unproven": 0}
}
```

`{"candidates": [], "dropped": {…}}` is a complete answer. `rule_source` with `«…»` is grepped back in the named file — copy the line, don't restate it.
