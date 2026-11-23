#!/usr/bin/env python3
"""check_merge_symbols.py — структурная сверка символов после авто-мержа.

Ловит то, что не видно ни в маркерах, ни в tsc: авто-мерж вставляет вторую
копию метода (вторая затирает первую), либо одна сторона переименовала метод,
и удаление уехало в неконфликтную зону — символ пропал молча. Ещё ловит
задвоенные имена в импортах, экспортных списках, полях interface и
деструктуризации — их tsc показывает только на полном прогоне.

Читает: рабочую копию (вне конфликтных зон) + версии сторон из индекса.
Ничего не изменяет: ни индекс, ни файлы.

Usage: python3 scripts/check_merge_symbols.py [путь ...] [--files список.txt ...]
Без путей — все файлы мержа; после резолва передай резолвнутые и правленые.
"""

import argparse
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from duplicates import new_duplicates  # noqa: E402
from merge_history import (  # noqa: E402
    CODE_EXT, BlobReader, changed_between, fast_forward_side, git, ref_blob,
)

# ponytail: регекс-извлечение определений вместо парсера — берёт JS/TS-методы,
# object-literal-методы Backbone, классы, const-функции и PHP-функции.
# Хватает, чтобы поймать дубль и пропажу; если начнёт врать — LSP documentSymbol.
# Регексы идут по всему тексту в режиме MULTILINE: сигнатура, разбитая prettier
# на несколько строк, иначе пропадает на одной стороне и даёт ложный ПРОПАЛ.

# Список параметров с одним уровнем вложенных скобок: `[^)]*` не проходит сквозь
# дефолт вида `date = new Date()` и объявляет символ пропавшим. Глубже одного
# уровня — уже к LSP documentSymbol.
PARAMS = r'\((?:[^()]|\([^()]*\))*\)'

# Тип возврата стрелки: `): Promise<void> =>`. Без `=` и `{`, иначе съест тело.
ARROW_RET = r'(?:\s*:[^=;{]+)?'

DEFS = [
    re.compile(r'^[ \t]*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)', re.M),
    re.compile(r'^[ \t]*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function\b', re.M),
    re.compile(r'^[ \t]*(?:export[ \t]+)?(?:(?:public|private|protected|static|async|function)[ \t]+)*'
               r'([A-Za-z_$][\w$]*)[ \t]*' + PARAMS + r'\s*(?::[^{;]+)?\{', re.M),
    re.compile(r'^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*'
               r'(?:async\s+)?(?:function\b|' + PARAMS + ARROW_RET + r'\s*=>|[A-Za-z_$][\w$]*\s*=>)',
               re.M),
]

KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'do', 'else',
    'try', 'typeof', 'new', 'delete', 'await', 'yield', 'constructor', 'super',
    'foreach', 'elseif',
}

MARK_START, MARK_BASE, MARK_MID, MARK_END = '<<<<<<<', '|||||||', '=======', '>>>>>>>'


def without_markers(lines, skip_conflicts):
    """Текст без строк-маркеров; skip_conflicts=True — и без всего между ними.

    Номера строк сохраняются: убранная строка становится пустой.
    """
    kept, inside = [], False
    for line in lines:
        s = line.lstrip()
        if s.startswith(MARK_START):
            inside = True
        elif s.startswith(MARK_END):
            inside = False
        marker = s.startswith((MARK_START, MARK_BASE, MARK_MID, MARK_END))
        kept.append('\n' if marker or (inside and skip_conflicts) else line)
    return ''.join(kept)


def defs_in(lines, skip_conflicts):
    """Имена определений. skip_conflicts=True — игнорировать всё между маркерами."""
    text = without_markers(lines, skip_conflicts)

    # Одна строка — одно определение: первый регекс из DEFS, совпавший на ней.
    by_line = {}
    for rx in DEFS:
        for m in rx.finditer(text):
            if m.group(1) in KEYWORDS:
                continue
            by_line.setdefault(text.count('\n', 0, m.start()), m.group(1))
    return [by_line[k] for k in sorted(by_line)]


def merge_scope(ours, theirs, result):
    """Файлы, где мерж мог уронить или задвоить символ.

    Файл, который меняла только одна сторона, git берёт с неё целиком —
    терять там нечего. Остаются:
    - файлы, которые меняли обе стороны после merge-base (с каждой из баз:
      у сборочных веток их несколько);
    - файлы, где результат отличается от обоих родителей: правка руками.

    result=None — рабочая копия.
    """
    bases = git('merge-base', '--all', ours, theirs).split()
    ours_changed, theirs_changed = set(), set()
    for base in bases:
        ours_changed |= changed_between(base, ours)
        theirs_changed |= changed_between(base, theirs)
    edited = changed_between(ours, result) & changed_between(theirs, result)
    return sorted((ours_changed & theirs_changed) | edited)


def scope():
    """Файлы мержа и откуда брать версии сторон.

    Два режима: мерж в процессе (стороны в индексе, :2/:3) и мерж уже
    закоммичен авто-мержем без конфликтов (стороны — родители HEAD).
    """
    parents = git('rev-parse', 'HEAD^@').split()
    unmerged = git('diff', '--name-only', '--diff-filter=U').splitlines()
    is_merge = bool(git('rev-parse', '--verify', '-q', 'MERGE_HEAD').strip())
    in_progress = (is_merge or unmerged
                   or bool(git('rev-parse', '--verify', '-q', 'CHERRY_PICK_HEAD').strip()))

    if in_progress or len(parents) < 2:
        # У неконфликтного файла stage-записей в индексе нет — версии сторон
        # берём из ref'ов. Именно там живёт «ренейм уехал в неконфликтную зону».
        if is_merge:
            other = 'MERGE_HEAD'
            files = unmerged + merge_scope('HEAD', 'MERGE_HEAD', None)
        else:
            other = 'CHERRY_PICK_HEAD'
            files = unmerged + git('diff', '--name-only', 'HEAD').splitlines()
        return files, [(':2:', 'ours'), (':3:', 'theirs'),
                       ('HEAD:', 'ours'), (f'{other}:', 'theirs')], ('HEAD', other)

    files = merge_scope(parents[0], parents[1], 'HEAD')
    return files, [(f'{parents[0]}:', 'ours'), (f'{parents[1]}:', 'theirs')], \
        (parents[0], parents[1])


def is_fast_forward(path, refs):
    """Результат совпадает с одной стороной, а версия другой есть в её истории.

    Пропажа в таком файле — правка более новой стороны, а не потеря при мерже.
    """
    ours_ref, theirs_ref_name = refs
    ours_blob, theirs_blob = ref_blob(ours_ref, path), ref_blob(theirs_ref_name, path)
    result = git('hash-object', '--', path).strip()
    # Отсев до `git log`: перемотка возможна, только если результат совпал с одной из сторон.
    if result not in {ours_blob, theirs_blob}:
        return False
    side = fast_forward_side(ours_ref, theirs_ref_name, path, ours_blob, theirs_blob)
    return side is not None and result == (ours_blob if side == 'ours' else theirs_blob)


def removed_by(path, name, where, refs):
    """Коммит другой стороны, который менял число вхождений name в файле: 'side sha «тема»'.

    Пропажу символа, который был только на одной стороне, могла внести
    другая сторона своим коммитом — тогда это её правка, а не потеря при мерже.
    """
    if len(where) != 1:
        return None
    ours_ref, theirs_ref_name = refs
    other, rng = (('theirs', f'{ours_ref}..{theirs_ref_name}') if 'ours' in where
                  else ('ours', f'{theirs_ref_name}..{ours_ref}'))
    first = git('log', '-1', '--format=%H%x09%s', f'-S{name}', rng, '--', path).strip()
    if not first:
        return None
    sha, _, subject = first.partition('\t')
    return f'{other} {sha[:8]} «{subject}»'


def requested_files(args):
    """Файлы из аргументов: пути и списки `--files`. Пусто — скоуп мержа."""
    files = list(args.paths)
    for listing in args.files or []:
        files += Path(listing).read_text().splitlines()
    return [f for f in files if f.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='*', help='проверить только эти файлы')
    parser.add_argument('--files', action='append',
                        help='файл со списком путей, по одному в строке; можно несколько')
    args = parser.parse_args()

    raw, sides, refs = scope()
    raw = requested_files(args) or raw
    files = [f for f in dict.fromkeys(raw) if f.endswith(CODE_EXT)]

    if not files:
        print('Кодовых файлов в мерже нет — структурная сверка не нужна.')
        return

    print(f'СТРУКТУРНАЯ СВЕРКА СИМВОЛОВ ({len(files)} файлов)')
    print('=' * 60)
    blobs = BlobReader()
    findings = []

    for f in files:
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                work = fh.readlines()
        except OSError:
            continue

        present = set(defs_in(work, False))
        lost = {}
        side_max = Counter()
        side_texts = []
        for slot, label in sides:
            side = blobs.read(f'{slot}{f}')
            if not side:
                continue
            side_text = side.decode('utf-8', errors='replace')
            side_texts.append(side_text)
            side_defs = defs_in(side_text.splitlines(True), False)
            side_max |= Counter(side_defs)
            # Пропажа: символ был на стороне, но в результате его нет нигде.
            for n in set(side_defs) - present:
                lost.setdefault(n, set()).add(label)

        # Дубли ищем ВНЕ конфликтных зон: внутри маркеров две копии — норма,
        # это и есть неразрешённый конфликт. Опасен дубль, который авто-мерж
        # вставил в неконфликтную зону. Дубль, который уже был на одной из
        # сторон (фикстуры, перегрузки), мерж не вносил — его не показываем.
        dups = [(n, c, 'второе затирает первое') for n, c in Counter(defs_in(work, True)).items()
                if c > 1 and c > side_max[n]]
        dups += [(f'{kind} {name}', c, 'имя задвоено, на сторонах реже') for kind, name, c
                 in new_duplicates(f, without_markers(work, True), *side_texts)]

        if dups or lost:
            findings.append((f, dups, lost))

    blobs.close()

    # История файла — до секунды `git log` на сторону; файлы независимы.
    with ThreadPoolExecutor(max_workers=8) as pool:
        forwards = list(pool.map(lambda item: bool(item[2]) and is_fast_forward(item[0], refs),
                                 findings))
        losses = [(f, n, where) for (f, _, lost), is_forward in zip(findings, forwards)
                  if not is_forward for n, where in lost.items()]
        origins = {(f, n): origin for (f, n, _), origin
                   in zip(losses, pool.map(lambda loss: removed_by(*loss, refs), losses))}

    clean = True
    for (f, dups, lost), is_forward in zip(findings, forwards):
        if is_forward:
            lost = {}
        if not dups and not lost:
            continue

        clean = False
        print(f'\n{f}')
        for n, c, what in sorted(dups):
            print(f'  ДУБЛЬ    {n} — {c} раза вне маркеров; {what}')
        for n, where in sorted(lost.items()):
            origin = origins.get((f, n))
            tail = f'; менял коммит {origin}' if origin else ''
            print(f'  ПРОПАЛ   {n} — есть в {"+".join(sorted(where))}, в результате нет{tail}')

    print()
    if clean:
        print('Дублей и пропаж не найдено.')
    else:
        print('Проверь каждую строку выше ПО ФАКТУ: дубль может быть перегрузкой,')
        print('пропажа — намеренным удалением или ренеймом. По .ts/.tsx уточняй')
        print('через LSP documentSymbol/findReferences, не по регексу.')
        print('«менял коммит» — последний коммит другой стороны с `git log -S<имя>` по файлу:')
        print('прочитай его `git show`, чтобы отличить удаление от правки вызова.')


if __name__ == '__main__':
    sys.exit(main())
