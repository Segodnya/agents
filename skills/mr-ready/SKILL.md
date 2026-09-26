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
1 REVIEW   review-staged branch --base <target> --mr <url> --checklist …
2 EXIT?    0 открытых → 6 · те же, что в r-1 → 6 (stuck) · r = 10 → 6 (cap)
3 POST     новые находки → треды `🤖 self-review ·`
4 FIX      audit-reply <url> --auto --commit --base <target>
5 SHIP     push → ответы в треды → discuss.md не пуст → один AskUserQuestion
6 HANDOFF  описание MR → отчёт в чат
```

Invocation: `mr-ready [<MR url>] [--no-spec]`. Один слэш-вызов на сообщение, поэтому два сообщения:
сначала `/goal`, дождаться остановки, потом `/mr-ready`.

```
1› /goal MR из отчёта mr-ready готов: последний раунд review-staged без открытых находок, проверки зелёные, ветка запушена, ответы в тредах — либо mr-ready сам сказал «стоп»
2› /mr-ready https://git.example.com/group/project/-/merge_requests/41
```

Без `/goal` цикл тоже работает; обёртка не даёт сессии бросить задачу после паузы на вопрос.

`SKILL_DIR` — из строки `Base directory for this skill:`; соседи — `SKILL_DIR/../review-staged`,
`SKILL_DIR/../audit-reply`. `MR_DIR` = `/tmp/mr-ready-<repo>-<iid>` — литеральный путь в каждой
команде. Состояние в `MR_DIR/state.md` (раунд, отчёты, `finding → discussion_id`); после `--resume`
продолжай с него.

## Права

Скилл **коммитит, пушит и постит** — разрешено строкой в `~/.claude/CLAUDE.md`. Никогда: `--force`,
`--amend`, `rebase`, `git add -A`, резолв тредов, assignee/labels/Draft.

## 0. Ground

1. MR: URL из инвокации, иначе `glab mr list --source-branch $(git branch --show-current)`. Нет → стоп.
   `glab mr view <iid> -F json` → `source_branch`, `target_branch`, `description`.
   Текущая ветка ≠ `source_branch` → вопрос.
2. База — `target_branch`, не master. `git fetch origin <target_branch>`.
3. Чек-лист: `--no-spec` → нет; есть в описании MR → `MR_DIR/checklist.md` дословно; иначе вопрос.
4. Проверки: бинарники в репо (`node_modules/.bin/tsc|eslint|jest|vitest`, `Makefile`, `pytest`)
   плюс `.claude/rules` / `CLAUDE.md` проекта. Не нашёл → вопрос.
5. Язык тредов — язык MR; человеку — на языке его комментария.

Все вопросы — в **одном** `AskUserQuestion`. Дальше тишина до DISCUSS или конца.

## 1. Review

```
Skill review-staged: branch --base <target_branch> --mr <url> --checklist MR_DIR/checklist.md   (или --no-spec)
```

Путь отчёта → `state.md`. **Открытые находки** = P0 + P1 + P2 + design notes + тикеты 🎫 минус
`✅ снято тредом`.

## 2. Exit?

- 0 открытых → шаг 6, `ready`.
- `r ≥ 2` и множество `(file, line±5, суть)` совпадает с `r-1` → шаг 6, `stuck`.
- `r = 10` → шаг 6, `cap`.

## 3. Post

Новая находка (нет треда с тем же `file:line±10` и сутью в `RS_DIR/threads.json`) → тред. Тело —
заголовок с маркером, цитата, `Проверено`, source, как в отчёте:

```bash
python3 "SKILL_DIR/scripts/mr_post.py" thread --url "<url>" --file <file> --line <line> --body-file MR_DIR/r<r>-f<n>.md
```

Без `file:line` → `glab mr note <iid> -m` с тем же маркером. Тикет 🎫 → тред «вне скоупа, черновик
тикета: …». Постинг упал → в `_Прочее_` отчёта, не повтор. `finding → discussion_id` → `state.md`.

## 4. Fix

```
Skill audit-reply: <url> --auto --commit --base <target_branch>
```

Берёт все открытые треды: свои `🤖 self-review` и людей. Триггеры DISCUSS — в audit-reply (`--auto`).
Вне диффа править можно только когда тронутая функция — общий корень; иначе тикет, Jira не создаём.

Выход: коммиты по одному на правку, `AR_DIR/replies.json`, `AR_DIR/discuss.md`, откаты.

## 5. Ship

1. `git push origin HEAD`. Отказ → стоп, `blocked`, историю не чинить.
2. После push — ответы из `replies.json`:
   ```bash
   python3 "SKILL_DIR/scripts/mr_post.py" reply --url "<url>" --discussion <id> --body-file MR_DIR/r<r>-a<n>.md
   ```
3. `discuss.md` не пуст → один `AskUserQuestion` на все пункты: «Править / Ответить / Отложить до
   ревьюера». Ответы — тем же путём: правка → коммит → push → тред. «Отложить» → в отчёт «ждёт ревьюера».
4. `round: r` → шаг 1.

## 6. Handoff

Описание MR — блок между `<!-- mr-ready -->`, апсерт:

```markdown
### Summary
<diff-summary для origin/<target_branch>...HEAD>

### Self-review
<r> раундов · тредов открыто: <n> · поправлено: <f> · отвечено: <k> · откатов: <v> · ждёт ревьюера: <d>
```

```bash
python3 "SKILL_DIR/scripts/mr_post.py" describe --url "<url>" --body-file MR_DIR/describe.md
```

Отчёт в чат:

```markdown
# mr-ready — MR !<iid> · <ready | stuck | cap | blocked>
Раундов: <r> · коммитов: <c> · тредов открыто: <n>, ответов: <k>

## Открыто для ревьюера
- `file:line` — суть · <ждёт ответа / отложено / откатил: причина>

## Черновики тикетов
- **<заголовок>** — что, где

## Проверки
<команды, итог>

Отчёты раундов: <пути>
```

Не `ready` → первой строкой причина и что решить человеку.

## Hard constraints

- Вопросы — только в шаге 0 и на DISCUSS.
- Коммит — только зелёный, только файлы правки. Красный после повтора → откат и ответ в тред.
- Ответ в тред — только после push коммита, на который он ссылается.
- `mr_post.py` сверяет каждую запись листингом; `exit 0` от `glab` — не доказательство.
- Два одинаковых раунда подряд — стоп.
- `model: "sonnet"` у всех субагентов.
