# agents

Личные скиллы для Claude Code.

| Скилл | Что делает |
|---|---|
| [`code-review`](skills/code-review) | Параллельный ревью staged-diff через саб-агентов с adversarial-проверкой находок. |
| [`dev-feedback`](skills/dev-feedback) | Performance feedback на разработчика по merged-МР из GitLab (через `glab`). |
| [`local-debug`](skills/local-debug) | Расставляет `console.warn`-пробы и диагностирует баг по логам. |
| [`merge-resolve`](skills/merge-resolve) | Разрешает конфликты git (merge / rebase / cherry-pick / stash) по единому плану. |
| [`perf-review`](skills/perf-review) | Беспощадное performance-ревью работы с Claude Code за N дней. |
| [`tech-task`](skills/tech-task) | Низкоуровневое ТЗ для фронтенд-задачи. |

## Подключить глобально

```bash
ln -s "$PWD/skills/<name>" ~/.claude/skills/<name>
```

## Установить через skills.sh

```bash
# весь репозиторий (CLI сам покажет список скиллов)
npx skills add Segodnya/agents

# конкретный скилл по прямой ссылке
npx skills add https://github.com/Segodnya/agents/tree/main/skills/code-review
```
