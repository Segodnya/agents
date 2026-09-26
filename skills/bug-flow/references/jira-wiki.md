# Jira: wiki-разметка через REST

`jira issue comment add` / `create --template` гонят текст через markdown: `#` становится заголовком, списки после `*жирного*` без пустой строки склеиваются в абзац, `<тег>` вырезается, редактирования нет. REST v2 принимает wiki строкой.

## Путь

1. Тело в файл в wiki-разметке.
2. Создать: `jira issue comment add <KEY> --template <file> --no-input < /dev/null` (без `< /dev/null` виснет). Id — из `jira issue view <KEY> --raw`.
3. Перезаписать: `python3 scripts/jira_put.py comment <KEY> <id> <file>`; описание — `jira_put.py description <KEY> <file>`.
4. Проверить структуру через `--raw` (heading / bulletList / orderedList / codeBlock).

Скрипт берёт `server`/`login` из `~/.config/.jira/.config.yml`, токен из `JIRA_API_TOKEN` или keychain `jira-cli`.

## Разметка

- Пустая строка вокруг списков.
- `#`-список после `{code}` начинается заново — текст после кода выносить абзацем.
- Вложенные пункты `**`; моно `{{…}}`; код `{code:js}`; заголовки `h3.`.
- HTML-теги словами («аудио-элемент»). Ключ тикета в тексте сам становится ссылкой.

## Тикеты и связи

- `jira issue create -p <PROJECT> -t Баг -s "…" --template <file> --no-input < /dev/null`, затем `jira_put.py description`.
- Типы связей локализованы: `jira issue link <A> <B> "3 - Относится"`. Список — в ошибке на неверное имя.
- PUT заменяет тело целиком, истории нет — перечитать перед перезаписью.
