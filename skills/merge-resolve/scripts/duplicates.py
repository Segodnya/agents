"""duplicates.py — дубли имён, которые оставляет мерж в JS/TS-перечислениях.

Авто-мерж и 3-way черновик склеивают две правки одного списка, если каждая
добавила свою строку: импорт из двух модулей, реэкспорт, поле interface,
деструктуризация с дефолтом. tsc это ловит, но только на Шаге 6 и списком
по всему репо; здесь дубль виден сразу по файлу.

Регекс-эвристика: строки и шаблоны не разбирает, вложенность считает по скобкам.
"""

import re
from collections import Counter

from merge_history import CODE_EXT

NAME = r'[A-Za-z_$][\w$]*'
JS_EXT = tuple(ext for ext in CODE_EXT if ext != '.php')

# Строки идут в альтернативе первыми: `'image/*'` иначе открывает комментарий
# до ближайшего `*/` и съедает закрывающие скобки.
STRINGS_OR_COMMENTS = re.compile(
    r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`"
    r'|(?P<comment>/\*.*?\*/|//[^\n]*)', re.S)
IMPORT = re.compile(r'\bimport\s+(?:type\s+)?(?!type\b)([^\'";]*?)\s*\bfrom\s*[\'"]', re.S)
EXPORT_LIST = re.compile(r'\bexport\s+(?:type\s+)?\{')
INTERFACE = re.compile(r'\binterface\s+' + NAME + r'\s*(?:<[^{]*?>)?\s*(?:extends\s+[^{]+)?\{')
TYPE_LITERAL = re.compile(r'\btype\s+' + NAME + r'\s*(?:<[^=]*?>)?\s*=\s*\{')
DESTRUCTURE = re.compile(r'\b(?:const|let|var)\s+\{')
PAIRS = {'{': '}', '(': ')', '[': ']'}


def block_items(text, open_at):
    """Элементы верхнего уровня блока `{…}`, начатого в open_at: делит по `,` `;` и переводу строки."""
    depth, items, start = 0, [], open_at + 1
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch in PAIRS:
            depth += 1
        elif ch in PAIRS.values():
            depth -= 1
            if depth == 0:
                items.append(text[start:i])
                break
        elif depth == 1 and ch in ',;\n':
            items.append(text[start:i])
            start = i + 1
    return [item.strip() for item in items if item.strip()]


def spec_name(item):
    """`a`, `type a`, `a as b` → имя связывания (`b`)."""
    item = re.sub(r'^type\s+', '', item)
    name = re.split(r'\s+as\s+', item)[-1].strip()
    return name if re.fullmatch(NAME, name) else None


def import_names(clause):
    """Локальные имена import: `X`, `* as X`, `X, { a, b as c }`, `{ a }`."""
    names = []
    head, _, rest = clause.partition('{')
    for part in head.split(','):
        part = part.strip()
        if part:
            names.append(spec_name(part.replace('*', '').strip()))
    if rest:
        names.extend(spec_name(item) for item in re.split(r'[,\n]', rest.split('}')[0]))
    return [n for n in names if n]


def field_name(item):
    """Поле interface или type-литерала; сигнатуры методов — None: перегрузки законны."""
    m = re.match(r'(?:readonly\s+)?(' + NAME + r')\s*\??\s*(.)', item + ' ')
    return m.group(1) if m and m.group(2) == ':' else None


def binding_name(item):
    """Имя связывания в деструктуризации: `a`, `a = 1`, `a: b`, `a: b = 1`; вложенная — None."""
    m = re.match(r'(?:\.\.\.)?(' + NAME + r')\s*(?::\s*(' + NAME + r')\b)?', item)
    if not m or re.match(NAME + r'\s*:\s*[{\[]', item):
        return None
    return m.group(2) or m.group(1)


def duplicate_names(path, text):
    """Счётчик дублей: {(вид, имя): число}, только где имя встречается больше раза.

    Импорты и экспортные списки считаются по файлу целиком, поля и
    деструктуризация — по блоку.
    """
    if not path.endswith(JS_EXT) or not text:
        return Counter()
    # Комментарий вырезаем, строку — до пустых кавычек: скобки внутри строк
    # иначе сбивают подсчёт вложенности. Переводы строк остаются.
    text = STRINGS_OR_COMMENTS.sub(
        lambda m: '\n' * m.group(0).count('\n') if m.group('comment')
        else m.group(0)[0] * 2 + '\n' * m.group(0).count('\n'), text)
    found = Counter()
    for m in IMPORT.finditer(text):
        found.update(('import', n) for n in import_names(m.group(1)))
    for m in EXPORT_LIST.finditer(text):
        found.update(('export', n) for n in map(spec_name, block_items(text, m.end() - 1)) if n)

    for rx, kind, name_of in ((INTERFACE, 'поле', field_name), (TYPE_LITERAL, 'поле', field_name),
                              (DESTRUCTURE, 'деструктуризация', binding_name)):
        for m in rx.finditer(text):
            block = Counter(n for n in map(name_of, block_items(text, m.end() - 1)) if n)
            for n, c in block.items():
                if c > 1:
                    found[(kind, n)] = max(found[(kind, n)], c)

    return Counter({key: c for key, c in found.items() if c > 1})


def new_duplicates(path, result, *sides):
    """Дубли результата, которых не было ни на одной стороне: [(вид, имя, число)]."""
    have = Counter()
    for side in sides:
        have |= duplicate_names(path, side)
    return sorted((kind, name, c) for (kind, name), c in duplicate_names(path, result).items()
                  if c > have[(kind, name)])
