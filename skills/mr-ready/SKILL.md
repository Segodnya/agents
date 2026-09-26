---
name: mr-ready
description: >
  Автономный цикл подготовки GitLab MR к ревью человеком: review-staged → находки тредами в MR →
  audit-reply правит или отвечает по рекомендации → коммит на правку, push, ответы в треды →
  повтор, пока находок не останется (кап 10 раундов). Спрашивает только продуктовые развилки.
  Используй на `/mr-ready`, «подготовь MR к ревью», «доведи MR до аудита», «make the MR ready
  for review», «self-review loop».
trigger: /mr-ready
---

# mr-ready

```
0 GROUND   MR · база · чек-лист · проверки → один вопрос
─ раунд r = 1…10 ─
1 REVIEW   Agent reviewer → Skill review-staged → отчёт + тела тредов
2 EXIT?    0 открытых → 6 · те же, что в r-1 → 6 (stuck) · r = 10 → 6 (cap)
3 POST     новые находки → треды `🤖 self-review ·`
4 FIX      Agent fixer → Skill audit-reply --auto --commit → коммиты, replies.json, discuss.md
5 SHIP     push → ответы в треды → discuss.md не пуст → один AskUserQuestion
6 HANDOFF  описание MR → отчёт в чат
```

Invocation: `mr-ready [<MR url>] [--no-spec]`. Один слэш-вызов на сообщение, поэтому два сообщения:
`/goal`, дождаться остановки, `/mr-ready`. Без `/goal` цикл тоже работает.

```
1› /goal MR из отчёта mr-ready готов: последний раунд review-staged без открытых находок (нерезолвленный тред с ответом — не находка, резолвит ревьюер), проверки зелёные, ветка запушена, ответы в тредах — либо mr-ready сам сказал «стоп». Чек-лист отгрузки: https://jira.example.com/browse/KEY-1?focusedCommentId=123
2› /mr-ready https://git.example.com/group/project/-/merge_requests/41
```

`SKILL_DIR` — из строки `Base directory for this skill:`. `MR_DIR` = `/tmp/mr-ready-<repo>-<iid>` —
литеральный путь в каждой команде. Без `--resume` запуск = с нуля: `rm -rf MR_DIR` до записи `state.md`, иначе старые `r<r>-f<n>.md` прошлого прогона путают reviewer.

## Роли

Ты — оркестратор: Ground, постинг, push, вопросы, `state.md`. Ревью и правки — два субагента
(`general-purpose`, **без `model`** — наследуют твою; `sonnet` только у агентов внутри скиллов),
новые на каждый раунд. Субагент не может спросить пользователя: упёрся → возвращает `MISSING: <что>`,
спрашиваешь ты. Отчёты, диффы, `threads.json` в твой контекст не попадают — только пути и строки.

`MR_DIR/state.md` — единственная память: url, база, чек-лист, проверки, раунд, отчёты,
`finding → discussion_id`. Пишется **до** каждого побочного действия. Первое действие после
`--resume`, компакта или паузы на вопрос — перечитать его.

## Права

Скилл коммитит, пушит и постит — разрешено в `~/.claude/CLAUDE.md`. Никогда: `--force`, `--amend`,
`rebase`, `git add -A`, резолв тредов, assignee/labels/Draft.

## 0. Ground

1. MR: URL из инвокации, иначе `glab mr list --source-branch $(git branch --show-current)`; нет → стоп.
   `glab mr view <iid> -F json` → `source_branch`, `target_branch`, `description`. Ветка ≠
   `source_branch` → вопрос.
2. База — `target_branch`, не master. `git fetch origin <target_branch>`.
3. Чек-лист → `MR_DIR/checklist.md` дословно. Источник по приоритету: `--no-spec` (нет); ссылка в
   `/goal` или инвокации; описание MR; иначе вопрос. Ссылка Jira `…/browse/<KEY>?focusedCommentId=<id>`
   → `jira issue view <KEY> --comments 50 --plain`, блок «Чек-лист для отгрузки» до следующего автора.
4. Проверки: бинарники репо (`node_modules/.bin/tsc|eslint|jest|vitest`, `Makefile`, `pytest`) +
   `.claude/rules` / `CLAUDE.md` проекта. Не нашёл → вопрос.
5. Язык тредов — язык MR; человеку — язык его комментария.

Все вопросы — в **одном** `AskUserQuestion`. Дальше тишина до DISCUSS или конца.

## 1. Review

Промпт reviewer:

> Вызови `Skill review-staged` с аргументами `branch --base <target_branch> --mr <url> --checklist
> MR_DIR/checklist.md` (или `--no-spec`). Вопросов не задавай — чего-то нет → `MISSING: <что>`.
> Для каждой открытой находки (P0/P1/P2, design note, тикет 🎫; не `✅ снято тредом`) запиши тело
> треда в `MR_DIR/r<r>-f<n>.md`: первая строка `🤖 self-review · <заголовок>`, дальше цитата,
> `Проверено`, source — дословно из отчёта; тикет — с пометкой «вне скоупа, черновик тикета».
> Ответь только: путь отчёта; по строке `f<n> · file:line · P? · суть` (без позиции — `general`);
> `open: <число>`.

Ответ → `state.md`. Никогда не ревьюй сам и не переиспользуй прошлого агента.

## 2. Exit?

- 0 открытых → шаг 6, `ready`. Нерезолвируемые ноты ботов (`bundle_size_analyzer`, CI) — не находки и не треды, в отчёт как `_Прочее_`.
- `r ≥ 2` и `(file, line±5, суть)` совпадают с `r-1` → шаг 6, `stuck`.
- `r = 10` → шаг 6, `cap`.

## 3. Post

Находка без треда с тем же `file:line±10` и сутью (индекс: `read_threads.py RS_DIR/threads.json`):

```bash
python3 "SKILL_DIR/scripts/mr_post.py" thread --url "<url>" --file <file> --line <line> --body-file MR_DIR/r<r>-f<n>.md
```

`general` → `glab mr note <iid> -m`. Постинг упал → в `_Прочее_` отчёта, не повтор.
`finding → discussion_id` → `state.md`.

## 4. Fix

Промпт fixer:

> Вызови `Skill audit-reply` с аргументами `<url> --auto --commit --base <target_branch>`. Вопросов
> не задавай — упёрся → `MISSING: <что>`. Вне диффа правь только когда тронутая диффом функция —
> общий корень; иначе тикет, Jira не создавай. Ответь только: коммиты (`hash · file:line · суть`),
> откаты (`file:line · причина`), пути `AR_DIR/replies.json` и `AR_DIR/discuss.md`.

Берёт все открытые треды — свои и людей. Триггеры DISCUSS — в audit-reply. Ответ → `state.md`.

## 5. Ship

1. `git push origin HEAD`. Отказ → стоп, `blocked`, историю не чинить.
2. Ответы из `replies.json` — только после push:
   ```bash
   python3 "SKILL_DIR/scripts/mr_post.py" reply --url "<url>" --discussion <id> --body-file MR_DIR/r<r>-a<n>.md
   ```
3. `discuss.md` не пуст → один `AskUserQuestion` на все пункты: «Править / Ответить / Отложить до
   ревьюера». Править → коммит → push → тред. Отложить → в отчёт.
4. `round: r` → шаг 1.

## 6. Handoff

Описание MR — апсерт блока `<!-- mr-ready -->`; в `describe.md` маркеров нет, скрипт оборачивает сам:

```markdown
### Summary
<diff-summary для origin/<target_branch>...HEAD>

### Self-review
<r> раундов · открыто: <o> · тредов на резолв: <n> · поправлено: <f> · отвечено: <k> · откатов: <v> · отложено: <d>
```

```bash
python3 "SKILL_DIR/scripts/mr_post.py" describe --url "<url>" --body-file MR_DIR/describe.md
```

Отчёт в чат; не `ready` → первой строкой причина и что решить человеку:

```markdown
# mr-ready — MR !<iid> · <ready | stuck | cap | blocked>
Раундов: <r> · коммитов: <c> · открыто: <o> · тредов на резолв: <n>

## Открыто для ревьюера
- `file:line` — суть · <отложено / откатил: причина>

## Черновики тикетов
- **<заголовок>** — что, где

## Проверки
<команды, итог>

Отчёты раундов: <пути>
```

## Hard constraints

- Вопросы — только в шаге 0 и на DISCUSS.
- Коммит — только зелёный, только файлы правки.
- Ответ в тред — только после push коммита, на который он ссылается.
- `mr_post.py` сверяет каждую запись листингом; `exit 0` от `glab` — не доказательство.
