# agents

Личные скиллы и команды для Claude Code.

| Скилл | Что делает |
|---|---|
| [`review-staged`](skills/review-staged) | Параллельный ревью диффа (staged / last / branch / worktree) саб-агентами, шардированными по файлам: каждая находка обязана нести дословную цитату из файла, остальное режет механический гейт; требует МР и чек-лист автора, снимает находки, уже объяснённые в тредах. |
| [`dev-feedback`](skills/dev-feedback) | Performance feedback на разработчика по merged-МР из GitLab (через `glab`). |
| [`audit-reply`](skills/audit-reply) | Разбирает комментарии аудитора в GitLab MR и по каждому помогает решить — править код или ответить обоснованием (через `glab`). |
| [`local-debug`](skills/local-debug) | Расставляет `console.warn`-пробы, а если баг только на билде — сниппеты в консоль прод-сборки; диагностирует по логам. |
| [`merge-resolve`](skills/merge-resolve) | Разрешает конфликты git (merge / rebase / cherry-pick / stash) по единому плану. |
| [`perf-review`](skills/perf-review) | Беспощадное performance-ревью работы с Claude Code за N дней. |
| [`tech-task`](skills/tech-task) | Низкоуровневое ТЗ для фронтенд-задачи. |

## Подключить глобально

```bash
ln -s "$PWD/skills/<name>" ~/.claude/skills/<name>
```

## Команды

| Команда | Что делает |
|---|---|
| [`diff-summary`](commands/diff-summary.md) | Однострочная сводка по дифу (`staged` / `branch` / `working`) — готовая к вставке в описание MR. |

Подключить глобально:

```bash
ln -s "$PWD/commands/<name>.md" ~/.claude/commands/<name>.md
```

## Установить через skills.sh

```bash
# весь репозиторий (CLI сам покажет список скиллов)
npx skills add Segodnya/agents

# конкретный скилл по прямой ссылке
npx skills add https://github.com/Segodnya/agents/tree/main/skills/review-staged
```
