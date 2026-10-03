#!/usr/bin/env python3
"""Инвентаризация и пересборка индекса файловой памяти Claude Code.

  memory.py projects            все каталоги памяти: записей, байт
  memory.py inventory <dir>     таблица записей + механические дефекты
  memory.py reindex <dir>       пересобрать MEMORY.md по файлам каталога
  memory.py selftest
"""
import re
import sys
import tempfile
from pathlib import Path

INDEX = 'MEMORY.md'
ENTRY = re.compile(r'^- \[(.+?)\]\(([^)]+\.md)\)')
HOOK_MAX = 160


def field(text, key):
    front = re.match(r'---\n(.*?)\n---', text, re.S)
    found = re.search(rf'^\s*{key}:\s*(.*)$', front.group(1), re.M) if front else None
    value = found.group(1).strip() if found else ''
    if len(value) > 1 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"')
    return value


def records(mem):
    return sorted(p for p in mem.glob('*.md') if p.name != INDEX)


def load(mem):
    return {p.name: p.read_text() for p in records(mem)}


def indexed(mem):
    index = mem / INDEX
    return index.read_text().splitlines() if index.exists() else []


def defects(mem, texts):
    names = {field(t, 'name') for t in texts.values()}
    listed = [m.group(2) for m in map(ENTRY.match, indexed(mem)) if m]
    found = []
    for name, text in texts.items():
        for key in ('name', 'description'):
            if not field(text, key):
                found.append(f'пустой {key}: {name}')
        # [[:<:]] и подобное — регэксп в тексте записи, не ссылка
        for link in sorted(set(re.findall(r'\[\[([\w-]+)\]\]', text))):
            if link not in names:
                found.append(f'ссылка в никуда: {name} → [[{link}]]')
    found += [f'в индексе, файла нет: {f}' for f in listed if f not in texts]
    found += [f'файл не в индексе: {f}' for f in texts if f not in listed]
    return found


def inventory(mem):
    texts = load(mem)
    rows = [(field(t, 'type') or '?', len(t.encode()), field(t, 'modified')[:10] or '-', name) for name, t in texts.items()]
    print('тип | байт | modified | файл')
    for kind, size, modified, name in sorted(rows, key=lambda r: (r[0], -r[1], r[2:])):
        print(f'{kind} | {size} | {modified} | {name}')
    total = sum(p.stat().st_size for p in mem.glob('*.md'))
    print(f'\nзаписей: {len(rows)}, байт с индексом: {total}')
    found = defects(mem, texts)
    print('\nдефекты:' if found else '\nдефектов нет')
    print('\n'.join(found))
    return 1 if found else 0


def reindex(mem):
    files = load(mem)

    def entry(name, title):
        hook = field(files[name], 'description')
        if len(hook) > HOOK_MAX:
            hook = hook[:HOOK_MAX - 1].rstrip() + '…'
        return f'- [{title or field(files[name], "name") or name}]({name}) — {hook}'

    out, seen = [], set()
    for line in indexed(mem):
        match = ENTRY.match(line)
        if not match:
            out.append(line)
        elif match.group(2) in files:
            out.append(entry(match.group(2), match.group(1)))
            seen.add(match.group(2))
    out += [entry(name, '') for name in files if name not in seen]
    (mem / INDEX).write_text('\n'.join(out).rstrip('\n') + '\n')
    print(f'{INDEX}: {len(files)} записей')
    return 0


def projects():
    root = Path.home() / '.claude' / 'projects'
    rows = []
    for mem in root.glob('*/memory'):
        files = records(mem)
        if files:
            rows.append((len(files), sum(p.stat().st_size for p in files), mem.parent.name))
    for count, size, name in sorted(rows, reverse=True):
        print(f'{count} записей | {size} байт | {name}')
    return 0


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Path(tmp)
        (mem / 'a.md').write_text('---\nname: a\ndescription: "про \\"a\\""\nmetadata:\n  type: project\n---\nсм. [[b]] и [[:<:]]\n')
        (mem / 'c.md').write_text('---\nname: ""\n---\nтело\n')
        (mem / INDEX).write_text('# Заголовок\n- [A](a.md) — старое\n- [Gone](gone.md) — нет файла\n')
        found = defects(mem, load(mem))
        assert 'ссылка в никуда: a.md → [[b]]' in found, found
        assert not any(':<:' in d for d in found), found
        assert 'в индексе, файла нет: gone.md' in found and 'файл не в индексе: c.md' in found, found
        assert 'пустой name: c.md' in found and 'пустой description: c.md' in found, found
        reindex(mem)
        assert (mem / INDEX).read_text() == '# Заголовок\n- [A](a.md) — про "a"\n- [c.md](c.md) — \n'
    print('ok')
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    commands = {'inventory': inventory, 'reindex': reindex}
    if args[:1] == ['projects']:
        sys.exit(projects())
    if args[:1] == ['selftest']:
        sys.exit(selftest())
    if len(args) == 2 and args[0] in commands and Path(args[1]).is_dir():
        sys.exit(commands[args[0]](Path(args[1])))
    sys.exit(__doc__)
