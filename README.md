# agents

Личные скиллы для Claude Code.

| Скилл | Что делает |
|---|---|
| [`code-review`](skills/code-review) | Параллельный ревью staged-diff через 5 саб-агентов. |
| [`local-debug`](skills/local-debug) | Расставляет `console.warn`-пробы и диагностирует баг по логам. |
| [`perf-review`](skills/perf-review) | Беспощадное performance-ревью работы с Claude Code за N дней. |
| [`tech-task`](skills/tech-task) | Низкоуровневое ТЗ для фронтенд-задачи. |

## Подключить глобально

```bash
ln -s "$PWD/skills/<name>" ~/.claude/skills/<name>
```
