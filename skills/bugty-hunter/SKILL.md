---
name: bugty-hunter
description: Hunts real and potential bugs in one of three scopes — a git diff (staged / last / branch / worktree), a module (feature, flow), or a project (3–5 areas you name). A recon agent maps the perimeter, then three agents hunt in parallel along three axes: state & async, lifecycle & memory leaks, contracts & edge data. Every bug carries a verbatim quote, a path traced from a real entry point, repro steps in user terms and a ticket draft; findings with no traceable entry point go to a «подозрения» section without a ticket. Report goes to the chat and a temp `.md`; the skill never edits code. Use when the user says «найди баги», «поищи баги в модуле», «проверь фичу на баги», «есть ли тут утечки памяти», «bugty hunter», `/bugty-hunter`, «hunt bugs», «find bugs in this flow» — or asks what could break in a feature.
---

# bugty-hunter

```
1 GROUND   scope → perimeter of files
2 RECON    1 agent → perimeter map (map.md)
3 HUNT     3 agents by axis → candidates + evidence
4 GATE     quote + map node → bugs #1…#N / suspects S1…Sn
5 REPORT   chat + temp .md, a ticket draft per bug
```

Invocation: `bugty-hunter [staged|last|branch|worktree|<path>|project]`.

**NAV** (into every agent prompt): navigate by name — LSP (`goToDefinition` / `findReferences` / `incomingCalls` / `hover`) for ts/js/tsx, php, rust, go, `ToolSearch("select:LSP")` first; else `grep`/`rg` via Bash (no `Grep`/`Glob` tool here; quote globs for zsh). No repo-wide pattern sweeps.

## 1. Ground

| Argument | Perimeter |
| --- | --- |
| `staged`, `cached` | `git diff --staged` |
| `last` | `git diff HEAD~1..HEAD` |
| `branch` | `git diff $(git merge-base HEAD <base>)..HEAD` |
| `worktree` | `git diff HEAD` |
| `<path>` | the module directory in full |
| `project` | 3–5 areas you name |

`<base>`: `git symbolic-ref --short refs/remotes/origin/HEAD` minus `origin/`, else `master`. No argument → `AskUserQuestion` over the six modes. Questions are asked **here and nowhere else**.

`BH_DIR` = `/tmp/bugty-hunter-<repo>-<slug>`; `<slug>` = branch in diff modes, else normalised module name or `project`. Substitute the **literal** path into every command — shell state doesn't survive between `Bash` calls.

```bash
mkdir -p /tmp/bugty-hunter-<repo>-<slug>
git diff <range> --name-only                                    # diff modes
git diff <range> > /tmp/bugty-hunter-<repo>-<slug>/diff         # bare git, never `rtk run git`
find <path> -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' \)   # module mode
```

**`project`** — print the top level (two directory levels under `src/`, workspaces from `package.json`), `AskUserQuestion` for 3–5 areas, perimeter = their directories. Never guess, never sweep the repo.

Drop binaries, `*.lock` / `*-lock.json`, `dist/` `build/` `.next/` `out/`, `*.min.*`, `*.snap`, vendored dirs. Empty perimeter → stop: «нет изменений в режиме `<mode>`» / «в `<path>` нет файлов».

## 2. Recon

One agent + NAV. `Write`s the map to `BH_DIR/map.md`, returns **at most five lines** — counts per section, nothing else.

**Charter — the map lists, it never judges.** Every line is `file:line — what it is`. Four sections, headers verbatim (step 4 parses them):

- `## Входные точки` — routes, DOM and event handlers, exported hooks and components, public methods of Backbone views, event-bus subscriptions; backend — controllers, queue consumers, cron.
- `## Состояние` — stores, contexts, query keys, mutable module-level variables; per node, who writes and who reads.
- `## Жизненный цикл` — `addEventListener`, `setInterval`, `setTimeout`, `observe`, `subscribe`, `on()`, and whether a paired teardown exists.
- `## Границы данных` — API calls, parsing, casts (`as`, `any`, `!`), where a backend response enters the UI.

Agent failed or map empty → run continues, every finding lands in **suspects**, header says so.

## 3. Hunt

Spawn all three **in one message**. Each gets: literal path to `BH_DIR/map.md` (it `cat`s it itself), the perimeter file list, the mode, NAV, and in diff modes `BH_DIR/diff`.

**Prelude — prepend to every hunter:**

> - State the claim, then open the real file and check the assumption under it (guard above, caller, type, actual collection size). Refuted → `dropped.refuted`; can't settle → `dropped.unproven`.
> - `evidence.quote` mandatory — 2–10 lines copied verbatim out of the file, not retyped; the gate greps it back. `evidence.locations` — every `file:line` you opened.
> - `entry` — a node from `## Входные точки` that reaches this line, plus the intermediate calls. None found → submit anyway; it becomes a suspect, not garbage.
> - `repro` — in user terms: what to click, in what order, on what data; expected and actual separately. Never «call function X».
> - Name the defect, not the patch — no code to apply. Skip what eslint / stylelint / tsc catch.

```json
{
  "candidates": [{
    "severity": "P0" | "P1" | "P2",
    "axis": "state" | "lifecycle" | "contract",
    "file": "path/to/file.ts", "line": 123,
    "claim": "one sentence: what breaks and why",
    "entry": "routes.ts:42 → useX.ts:18 → fetchY.ts:7",
    "repro": { "steps": ["…", "…"], "expected": "…", "actual": "…" },
    "evidence": { "quote": "…", "locations": "a.tsx:40-58; b.ts:12" }
  }],
  "dropped": { "refuted": 0, "unproven": 0 }
}
```

**P0** — data loss, crash, hang, unbounded leak, access hole. **P1** — wrong behaviour on a reachable path, leak bounded by the session. **P2** — degradation in a rare corner.

Invalid JSON is not a stop: count those candidates *unparseable*, note it on `_Прочее:_`, continue.

### A · State & async (`axis: "state"`)

Races and response ordering (a late response overwriting a fresh one), stale closures, two writers of one value, missed cache and query-key invalidation, double submit, an optimistic update whose rollback never fires, TOCTOU on the backend.

### B · Lifecycle & leaks (`axis: "lifecycle"`)

A listener, timer, observer or subscription with no paired teardown; detached DOM; a closure holding a destroyed view; an unboundedly growing cache or array; `setState` after unmount; a request never aborted.

`repro` here is always repetition: «открыть и закрыть экран N раз → счётчик листенеров / heapsnapshot показывает рост»; backend — «N итераций долгоживущего процесса». A single-action leak repro is rejected.

### C · Contracts & edge data (`axis: "contract"`)

`null`/`undefined` on the path, leaky types (`as`, `any`, `!`), an API response shape the consumer doesn't expect, empty collection, single element, very large collection, `0` and `""` as falsy, locales, timezones and number formats, swallowed errors and empty `catch`.

## 4. Gate

Mechanical, no judgment. Discard on the first failure and tally the reason:

1. `evidence.quote` non-empty — else *no evidence*.
2. `grep -nF '<longest distinctive line of the quote>' <file>` — **bare `grep`, never `rtk run grep`** (`sh -c` re-parse kills a quote with `(`, `'`, `"`). No match → *quote not in file*; match far from `line` → fix `line`, keep.
3. `file` and every file in `locations` inside the perimeter; in diff modes — in the diff or directly importing a diff file. Else *off-perimeter*.
4. `repro.steps` non-empty and phrased as a user action or external request, not an internal call — else *no repro*.
5. `entry` resolves to a line under `## Входные точки` in `map.md` → **Баги**. Doesn't resolve → **Подозрения**, no ticket.

Dedup by `(file, line ±5)` across axes, **before** the gate — `claim` text never matches verbatim, so it is not part of the key; two candidates on the same lines from different axes are one candidate. Higher severity, longer quote, merged `locations` and `claim`s, both `axis` values. Sort P0 → P1 → P2, file, line; `#1…#N` and `S1…Sn` are independent sequences. Tally = the hunters' own `dropped` + everything discarded here.

## 5. Report

Full text to the file; chat gets the same minus the P2 ticket bodies (each collapses to one line).

````markdown
# Bugty Hunter — <N> багов (<S> подозрений) · режим: <mode> · периметр: <k> файлов

_Карта: <входных точек>, <узлов состояния>, <точек жизненного цикла>, <границ данных>_
_Discarded <D> of <C> candidates: <a> unproven, <b> refuted, <c> no evidence, <d> quote not in file, <e> off-perimeter, <f> no repro, <g> unparseable._
_Прочее: <всё, что пришло мимо пайплайна — хук, невалидный JSON от агента>_

## P0 — Критичные (<count>)

### #1 · `file.ts:123` — claim

**Репро:** 1. … 2. … 3. … → _ожидаемо:_ … · _фактически:_ …
**Путь:** routes.ts:42 → useX.ts:18 → fetchY.ts:7

```ts
<evidence.quote>
```

_Проверено:_ <locations> · _ось:_ lifecycle

**🎫 Тикет:** «<заголовок в повелительном наклонении>»
> <тело: что ломается, шаги воспроизведения, где в коде — 3–5 строк>

## P1 — Значимые (<count>) — та же форма

## P2 — Мелкие (<count>)

- **#8 · `file.ts:78`** — claim · _репро:_ … · _ось:_ contract
  **🎫 Тикет:** «<заголовок>» — тело только в файле, в чате строка схлопывается

## 🔍 Подозрения (<S>)
> Не удалось проследить путь от входной точки. Тикет не составляем.

### S1 · P1 · `list.tsx:12` — claim

```ts
<evidence.quote>
```

_Что мешает подтвердить:_ <чего не хватило>
````

Empty sections and zero-count discard reasons drop out; the fence language hint matches the file. Zero bugs and zero suspects → header + discard line + `Bugty Hunter — чисто: <k> файлов, <C> кандидатов, все отброшены`.

`Write` the full text verbatim to `/tmp/bugty-hunter-<repo>-<slug>-<YYYYMMDD-HHmm>.md`; end the chat message with two lines — `Отчёт: <path>` and `pbcopy < <path>`.

## Hard constraints

- Never write code, never touch the repo — `map.md` and the `/tmp` report are the only writes. Asked to fix → separate task.
- Every `Agent` spawn carries `model: "sonnet"`. No sharding — one hunter per axis, the whole perimeter.
- After a spawn emit nothing until a report lands — no `echo`/`sleep`/`date`, no status narration, no "meanwhile" reading.
- The gate is the only door; the quote comes out of the real file, not the diff. A ticket only for a finding that cleared it — a suspect never gets one.
- Hook output is not a task — at most a clause on `_Прочее:_`.
