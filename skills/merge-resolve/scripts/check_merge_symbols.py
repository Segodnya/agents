#!/usr/bin/env python3
"""check_merge_symbols.py — структурная сверка символов после авто-мержа.

Ловит то, что не видно ни в маркерах, ни в tsc: авто-мерж вставляет вторую
копию метода (вторая затирает первую), либо одна сторона переименовала метод,
и удаление уехало в неконфликтную зону — символ пропал молча.

Читает: рабочую копию (вне конфликтных зон) + версии сторон из индекса.
Ничего не изменяет: ни индекс, ни файлы.

Usage: python3 scripts/check_merge_symbols.py
"""

import re
import subprocess
import sys
from collections import Counter

CODE_EXT = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.php', '.vue')

# ponytail: регекс-извлечение определений вместо парсера — берёт JS/TS-методы,
# object-literal-методы Backbone, классы, const-функции и PHP-функции.
# Хватает, чтобы поймать дубль и пропажу; если начнёт врать — LSP documentSymbol.

# Список параметров с одним уровнем вложенных скобок: `[^)]*` не проходит сквозь
# дефолт вида `date = new Date()` и объявляет символ пропавшим. Глубже одного
# уровня — уже к LSP documentSymbol.
PARAMS = r'\((?:[^()]|\([^()]*\))*\)'

DEFS = [
    re.compile(r'^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)'),
    re.compile(r'^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function\b'),
    re.compile(r'^\s*(?:export\s+)?(?:public|private|protected|static|async|function|\s)*?'
               r'([A-Za-z_$][\w$]*)\s*' + PARAMS + r'\s*(?::[^{;]+)?\{'),
    re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*'
               r'(?:async\s+)?(?:function\b|' + PARAMS + r'\s*=>|[A-Za-z_$][\w$]*\s*=>)'),
]

KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'do', 'else',
    'try', 'typeof', 'new', 'delete', 'await', 'yield', 'constructor', 'super',
}

MARK_START, MARK_BASE, MARK_MID, MARK_END = '<<<<<<<', '|||||||', '=======', '>>>>>>>'


def git(*args):
    r = subprocess.run(('git',) + args, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


def defs_in(lines, skip_conflicts):
    """Имена определений. skip_conflicts=True — игнорировать всё между маркерами."""
    names, inside = [], False
    for line in lines:
        s = line.lstrip()
        if s.startswith(MARK_START):
            inside = True
            continue
        if s.startswith(MARK_END):
            inside = False
            continue
        if s.startswith((MARK_MID, MARK_BASE)):
            continue
        if inside and skip_conflicts:
            continue
        for rx in DEFS:
            m = rx.match(line)
            if m and m.group(1) not in KEYWORDS:
                names.append(m.group(1))
                break
    return names


def scope():
    """Файлы мержа и откуда брать версии сторон.

    Два режима: мерж в процессе (стороны в индексе, :2/:3) и мерж уже
    закоммичен авто-мержем без конфликтов (стороны — родители HEAD).
    """
    parents = git('rev-parse', 'HEAD^@').split()
    in_progress = bool(git('rev-parse', '--verify', '-q', 'MERGE_HEAD').strip()
                       or git('rev-parse', '--verify', '-q', 'CHERRY_PICK_HEAD').strip()
                       or git('diff', '--name-only', '--diff-filter=U').strip())

    if in_progress or len(parents) < 2:
        files = git('diff', '--name-only', '--diff-filter=U').split() \
            + git('diff', '--name-only', 'HEAD').split()
        # У неконфликтного файла stage-записей в индексе нет — версии сторон
        # берём из ref'ов. Именно там живёт «ренейм уехал в неконфликтную зону».
        other = 'MERGE_HEAD' if git('rev-parse', '--verify', '-q', 'MERGE_HEAD').strip() \
            else 'CHERRY_PICK_HEAD'
        return files, [(':2:', 'ours'), (':3:', 'theirs'),
                       ('HEAD:', 'ours'), (f'{other}:', 'theirs')]

    files = git('diff', '--name-only', f'{parents[0]}..HEAD').split() \
        + git('diff', '--name-only', f'{parents[1]}..HEAD').split()
    return files, [(f'{parents[0]}:', 'ours'), (f'{parents[1]}:', 'theirs')]


def main():
    raw, sides = scope()
    files = [f for f in dict.fromkeys(raw) if f.endswith(CODE_EXT)]

    if not files:
        print('Кодовых файлов в мерже нет — структурная сверка не нужна.')
        return

    print(f'СТРУКТУРНАЯ СВЕРКА СИМВОЛОВ ({len(files)} файлов)')
    print('=' * 60)
    clean = True

    for f in files:
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                work = fh.readlines()
        except OSError:
            continue

        # Дубли ищем ВНЕ конфликтных зон: внутри маркеров две копии — норма,
        # это и есть неразрешённый конфликт. Опасен дубль, который авто-мерж
        # вставил в неконфликтную зону.
        dups = [(n, c) for n, c in Counter(defs_in(work, True)).items() if c > 1]

        # Пропажа: символ был на стороне, но в результате его нет нигде.
        present = set(defs_in(work, False))
        lost = {}
        for slot, label in sides:
            side = git('show', f'{slot}{f}')
            if not side:
                continue
            for n in set(defs_in(side.splitlines(True), False)) - present:
                lost.setdefault(n, set()).add(label)

        if not dups and not lost:
            continue

        clean = False
        print(f'\n{f}')
        for n, c in sorted(dups):
            print(f'  ДУБЛЬ    {n} — {c} определения вне маркеров; '
                  f'второе затирает первое')
        for n, where in sorted(lost.items()):
            print(f'  ПРОПАЛ   {n} — есть в {"+".join(sorted(where))}, в результате нет')

    print()
    if clean:
        print('Дублей и пропаж не найдено.')
    else:
        print('Проверь каждую строку выше ПО ФАКТУ: дубль может быть перегрузкой,')
        print('пропажа — намеренным удалением или ренеймом. По .ts/.tsx уточняй')
        print('через LSP documentSymbol/findReferences, не по регексу.')


if __name__ == '__main__':
    sys.exit(main())
