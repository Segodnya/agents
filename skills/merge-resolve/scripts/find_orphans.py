#!/usr/bin/env python3
"""find_orphans.py — файлы, которых нет на стороне, взятой в каталоге целиком.

Резолв «весь каталог берём с theirs» переписывает конфликтные файлы, но
файл, который ours добавил без конфликта, остаётся. Если theirs этот файл
убрал или переименовал при доработке, в результате он сирота: его никто
не импортирует, и ни tsc, ни тесты этого не покажут.

Кандидат — файл каталога, которого нет в дереве стороны. Ссылки ищутся
по имени модуля (`git grep -w -F`); ссылки из других кандидатов не считаются:
сирота часто приходит кластером с тестом и `index.ts`.

Ничего не изменяет.

Usage: python3 scripts/find_orphans.py --side theirs|ours DIR [DIR ...]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from merge_history import CODE_EXT, git, merge_sides, tree_args  # noqa: E402

MAX_SHOWN = 3


def module_name(path):
    """Имя, под которым файл импортируют: `a/b/x.types.ts` → `x.types`, `a/b/index.ts` → `b`."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.basename(os.path.dirname(path)) if stem == 'index' else stem


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--side', choices=('theirs', 'ours'), required=True,
                        help='сторона, которую взяли в каталогах целиком')
    parser.add_argument('dirs', nargs='+')
    args = parser.parse_args()

    sides = merge_sides()
    if not sides:
        print('Мержа нет: ни MERGE_HEAD, ни merge-коммита в HEAD.')
        return 0
    ours, theirs, result = sides
    side_ref = theirs if args.side == 'theirs' else ours

    extra = git('diff', '--name-only', '--no-renames', '--diff-filter=A',
                side_ref, *tree_args(result), '--', *args.dirs).splitlines()
    if not extra:
        print(f'Каталоги совпадают с {args.side} по составу файлов.')
        return 0

    candidates = set(extra)
    names = {path: module_name(path) for path in extra}
    # Один `git grep` на все имена: -z отделяет путь от совпадения, -o печатает само имя.
    out = git('grep', '-o', '-w', '-F', '-z', '-f', '-', *tree_args(result),
              '--', *[f'*{ext}' for ext in CODE_EXT], stdin='\n'.join(set(names.values())) + '\n')
    prefix = f'{result}:' if result else ''
    users = {}
    for line in out.splitlines():
        file, _, name = line.partition('\0')
        file = file.removeprefix(prefix)
        if file not in candidates:
            users.setdefault(name, set()).add(file)

    print(f'ФАЙЛЫ, КОТОРЫХ НЕТ В {args.side} ({len(extra)}):')
    for path in extra:
        hits = sorted(users.get(names[path], ()))
        if hits:
            more = f' (+{len(hits) - MAX_SHOWN})' if len(hits) > MAX_SHOWN else ''
            print(f'  {path} — ссылки: {", ".join(hits[:MAX_SHOWN])}{more}')
        else:
            print(f'  {path} — ссылок нет: сирота')
    print('Сирота — развилка в отчёте: удалить (`git rm`, это правка индекса) или оставить. '
          'Файл со ссылками — правка другой ветки, проверь, жив ли его потребитель.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
