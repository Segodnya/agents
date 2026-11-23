#!/usr/bin/env python3
"""classify_conflicts.py — делит конфликтные файлы на механические и ручные.

Классы:
- take-theirs: версия ours есть в истории theirs — у ours нет своих правок;
- take-ours: версия theirs есть в истории ours. Если новая сторона сама файл
  не правила и новее только через merge-коммит — manual;
- squash-theirs / squash-ours: каждый собственный коммит одной стороны по файлу
  лежит в ветке, которую другая сторона влила (обычно squash в GitLab) после
  этого коммита. Версия первой стороны — старая копия той же работы;
- realbase-clean: 3-way merge от последней версии файла, общей для обеих
  историй, проходит без конфликтов и не задваивает имён; черновик лежит в
  OUT/drafts/<path>;
- both-deleted: файл удалили обе стороны (ренейм на обеих) — `git rm`;
- manual: обе стороны правили одно место, нет общей версии (ренейм на одной
  стороне), delete/modify или бинарный файл. Черновик diff3 от реальной базы,
  если она есть, лежит там же. Файл, который трогал откат (`git revert`) в
  собственном диапазоне стороны, — всегда manual: черновик строится от версии
  до отката и принимает его, а блок ОТКАТЫ называет развилку.

Класс take-*/squash-* без файла на оставляемой стороне — это `git rm`; сводка
печатает их число. Причина по каждому ручному файлу — OUT/reasons.txt,
собственные коммиты сторон, которых не принёс squash, — OUT/side-commits.txt.

Виртуальная база `ort` при нескольких merge-base делает конфликтом весь файл,
даже если одна сторона просто старее другой. Реальная база это снимает.

Дальше скрипт ищет удалённые экспорты с потребителями: символ экспортировался
на одной из сторон, пропал из результата (черновика или авто-слитого файла),
а другой файл импортирует его из этого модуля. Так ловится «берём сущность из
master, а её старый контракт держит страница, которая есть только в ours».

Индекс и рабочую копию не трогает. Пишет только в OUT.

Usage: python3 scripts/classify_conflicts.py [--out DIR]
"""

import argparse
import functools
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from duplicates import NAME, new_duplicates, spec_name  # noqa: E402
from merge_history import (  # noqa: E402
    CODE_EXT, BlobReader, fast_forward_side, find_reverts, git, git_bytes, history_blobs,
    merged_branches, side_commits, squash_covered, stage_blob, theirs_ref,
)

TS_EXPORT = re.compile(
    r'^[ \t]*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?'
    r'(?:const\s+enum|const|let|var|function\*?|class|interface|type|enum|namespace)\s+('
    + NAME + ')', re.M)
TS_EXPORT_LIST = re.compile(r'^[ \t]*export\s+(?:type\s+)?\{([^}]*)\}', re.M)
PHP_EXPORTS = [
    re.compile(r'^[ \t]*(?:final\s+|abstract\s+|readonly\s+)*(?:class|interface|trait|enum)\s+(\w+)', re.M),
    re.compile(r'^[ \t]*(?:final\s+|abstract\s+)*public\s+(?:static\s+)?function\s+(\w+)', re.M),
    re.compile(r'^[ \t]*(?:final\s+)?(?:public\s+)?const\s+(\w+)\s*=', re.M),
]
MIN_NAME_LEN = 4
MAX_FILES_SHOWN = 3
# Сколько общих версий пробовать базой. Порядок полной истории (`--full-history`)
# перемешивает боковые ветки по дате, и первая общая версия не всегда ближайшая.
BASE_CANDIDATES = 5
LFS_POINTER = b'version https://git-lfs'


def exported_names(path, text):
    if text is None:
        return set()
    names = set()
    if path.endswith('.php'):
        for rx in PHP_EXPORTS:
            names.update(rx.findall(text))
        names.discard('__construct')
        return names
    names.update(TS_EXPORT.findall(text))
    for block in TS_EXPORT_LIST.findall(text):
        names.update(n for n in (spec_name(item.strip()) for item in block.split(',')) if n)
    return names


def decode(data):
    return None if data is None else data.decode('utf-8', errors='replace')


def is_binary(*blobs):
    """Бинарный файл или LFS-указатель: текстовый мерж указателей бессмыслен."""
    return any(b is not None and (b'\0' in b[:8000] or b.startswith(LFS_POINTER))
               for b in blobs)


def merge_file(base, ours, theirs):
    """git merge-file --diff3 в память. Возвращает (число конфликтов, текст)."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, data in (('ours', ours), ('base', base), ('theirs', theirs)):
            p = os.path.join(tmp, name)
            with open(p, 'wb') as fh:
                fh.write(data)
            paths.append(p)
        r = subprocess.run(['git', 'merge-file', '-p', '--diff3',
                            '-L', 'ours', '-L', 'realbase', '-L', 'theirs', *paths],
                           capture_output=True)
        if r.returncode < 0:
            raise RuntimeError(f'git merge-file: {r.stderr.decode()}')
        return r.returncode, r.stdout


LAYOUTS = {
    (1, 2): 'удалён в theirs, изменён в ours',
    (1, 3): 'удалён в ours, изменён в theirs',
    (2,): 'есть только в ours: theirs удалил или переименовал',
    (3,): 'есть только в theirs: ours удалил или переименовал',
}


class Sides:
    """Коммиты сторон по конфликтным файлам и что из них принесли squash-мержи.

    ours_commits / theirs_commits — не-merge коммиты собственного диапазона
    стороны по каждому файлу. ours_cover — коммиты theirs, которые принесли
    ветки, влитые в ours; theirs_cover — наоборот.
    """

    def __init__(self, ref, files, reverts):
        self.ours_commits = side_commits(f'{ref}..HEAD', files)
        self.theirs_commits = side_commits(f'HEAD..{ref}', files)
        by_side = {side: [r for r in reverts if r.side == side] for side in ('ours', 'theirs')}
        self.theirs_cover = squash_covered(
            merged_branches(f'HEAD..{ref}', by_side['theirs']), ref)
        self.ours_cover = squash_covered(
            merged_branches(f'{ref}..HEAD', by_side['ours']), 'HEAD')

    def squash_side(self, path):
        """Сторона, чьи squash-мержи уже несут все правки другой стороны в файле.

        'theirs' — каждый собственный коммит ours по файлу лежит в ветке, которую
        theirs влил после этого коммита: версия ours — старая копия той же работы.
        """
        ours = self.ours_commits.get(path, [])
        theirs = self.theirs_commits.get(path, [])
        by_theirs = bool(ours) and all(sha in self.theirs_cover for sha, _ in ours)
        by_ours = bool(theirs) and all(sha in self.ours_cover for sha, _ in theirs)
        if by_theirs == by_ours:
            return None
        return 'theirs' if by_theirs else 'ours'

    def uncovered(self, path):
        """Собственные коммиты сторон по файлу, которых не принёс squash другой стороны."""
        return ([('ours', sha, s) for sha, s in self.ours_commits.get(path, [])
                 if sha not in self.theirs_cover]
                + [('theirs', sha, s) for sha, s in self.theirs_commits.get(path, [])
                   if sha not in self.ours_cover])


def classify(path, ref, reverts, sides):
    """Класс файла, причина и текст результата для поиска удалённых экспортов."""
    revert = reverts.get(path)
    b1, b2, b3 = stage_blob(1, path), stage_blob(2, path), stage_blob(3, path)
    squash = sides.squash_side(path)

    if not b2 or not b3:
        layout = tuple(n for n, blob in ((1, b1), (2, b2), (3, b3)) if blob)
        if layout == (1,):
            return 'both-deleted', '', None, None
        reason = LAYOUTS.get(layout, 'delete/modify')
        if revert:
            return 'manual', f'{reason}; {revert_note(revert)}', None, None
        if squash:
            return f'squash-{squash}', '', None, None
        return 'manual', reason, None, None

    ours = git_bytes('cat-file', 'blob', b2)
    theirs = git_bytes('cat-file', 'blob', b3)

    # Откат — развилка для пользователя, а не механика: даже чистый черновик
    # молча выбирает «принять откат». Черновик от версии до отката переносит
    # ровно хунки отката поверх правок другой стороны.
    if revert:
        before = git_bytes('cat-file', 'blob', f'{revert.sha}^:{path}')
        note, draft = revert_note(revert), None
        if before is not None and not is_binary(ours, theirs):
            conflicts, draft = merge_file(before, ours, theirs)
            note += (f': черновик принимает откат (база {revert.sha[:8]}^), '
                     f'{conflicts} конфликт(ов)')
        return 'manual', note, None, draft

    if squash:
        return f'squash-{squash}', '', theirs if squash == 'theirs' else ours, None

    side = fast_forward_side('HEAD', ref, path, b2, b3)
    if side:
        # Новее та сторона, чья история прошла через версию другой. Если сама она
        # файл не правила, новая версия пришла merge-коммитом — часто старой копией
        # из другой ветки, а не доработкой.
        own = sides.ours_commits if side == 'ours' else sides.theirs_commits
        if not own.get(path):
            return 'manual', f'{side} новее только через merge-коммиты', None, None
        return f'take-{side}', '', theirs if side == 'theirs' else ours, None

    if is_binary(ours, theirs):
        return 'manual', 'бинарный файл или LFS-указатель', None, None

    ours_set = set(history_blobs('HEAD', path))
    candidates = [h for h in history_blobs(ref, path) if h in ours_set][:BASE_CANDIDATES]
    if not candidates:
        return 'manual', 'нет общей версии в историях (ренейм на одной стороне?)', None, None

    best = None
    for real_base in candidates:
        conflicts, draft = merge_file(git_bytes('cat-file', 'blob', real_base), ours, theirs)
        if best is None or conflicts < best[0]:
            best = (conflicts, draft, real_base)
        if conflicts == 0:
            break

    conflicts, draft, real_base = best
    if conflicts:
        return 'manual', f'{conflicts} конфликт(ов) от реальной базы {real_base[:8]}', None, draft
    dups = new_duplicates(path, decode(draft), decode(ours), decode(theirs))
    if dups:
        names = ', '.join(f'{kind} {name}' for kind, name, _ in dups)
        return 'manual', f'черновик от реальной базы задваивает имена «{names}»', None, draft
    return 'realbase-clean', '', draft, draft


CLASSES = ('take-theirs', 'take-ours', 'squash-theirs', 'squash-ours', 'realbase-clean',
           'both-deleted', 'manual')
# Стадия, которую класс оставляет. Её нет в индексе — сторона удалила файл: `git rm`.
KEEP_STAGE = {'take-theirs': 3, 'squash-theirs': 3, 'take-ours': 2, 'squash-ours': 2}


def reason_kind(reason):
    """Причина без чисел, хешей и «…»-подробностей (темы коммитов, имена) — ключ для сводки."""
    reason = re.sub(r'«[^»]*»', '«…»', reason)
    reason = re.sub(r'\b[0-9a-f]{7,40}\b', '<sha>', reason)
    return re.sub(r'\d+', 'N', reason)


def revert_note(revert):
    return f'откат на стороне {revert.side} {revert.sha[:8]} «{revert.subject}»'


def write_draft(out, path, data):
    target = Path(out) / 'drafts' / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def removed_exports(results, ref, blobs):
    """Экспорты, пропавшие из результата: {имя: [(файл, класс)]}.

    Версии сторон — из стадий индекса, у авто-слитого файла — из HEAD и ref.
    """
    removed = {}
    for path, cls, result in results:
        if not path.endswith(CODE_EXT):
            continue
        before = set()
        for stage, side_ref in ((2, 'HEAD'), (3, ref)):
            data = blobs.read(f':{stage}:{path}')
            if data is None:
                data = blobs.read(f'{side_ref}:{path}')
            before |= exported_names(path, decode(data))
        after = exported_names(path, decode(result))
        for name in before - after:
            if len(name) >= MIN_NAME_LEN:
                removed.setdefault(name, []).append((path, cls))
    return removed


TS_IMPORT = re.compile(r'(?:import|export)\s+(?:type\s+)?\{([^}]*)\}\s*from\s*[\'"]([^\'"]+)[\'"]')
PHP_USE = re.compile(r'^[ \t]*use\s+([\w\\]+?)(?:\s+as\s+\w+)?;', re.M)


def import_targets(name, hit, source, text):
    """Модули, через которые hit импортирует name из source.

    Возвращает список: None — путь указывает прямо на source; строка — каталог
    выше source (корень слайса), его `index` может реэкспортировать name из
    другого файла. TS: относительный путь резолвим от каталога hit; алиас
    (`@entities/X`) и голый путь (`lib/utils/x`) ищем как сегменты пути source.
    PHP: `use ...\\Name;`.
    """
    if source.endswith('.php'):
        return [None] if any(u.split('\\')[-1] == name for u in PHP_USE.findall(text)) else []
    src = os.path.splitext(source)[0]
    if src.endswith('/index'):
        src = src[:-len('/index')]
    targets = []
    for names, spec in TS_IMPORT.findall(text):
        if not re.search(r'(?<![\w$])' + re.escape(name) + r'(?![\w$])', names):
            continue
        if spec.startswith('.'):
            target = os.path.normpath(os.path.join(os.path.dirname(hit), spec))
            if src == target:
                targets.append(None)
            elif src.startswith(target + '/'):
                targets.append(target)
            continue
        spec = spec.lstrip('@').rstrip('/')
        if src.endswith('/' + spec):
            targets.append(None)
            continue
        at = ('/' + src + '/').find('/' + spec + '/')
        if at >= 0:
            targets.append(src[:at + len(spec)])
    return targets


def reexports(module_dir, name, exports_of):
    """index модуля или файл с этим путём экспортирует name — потребитель не сломан."""
    return any(name in exports_of(path)
               for ext in ('.ts', '.tsx', '.js', '.jsx', '.mjs')
               for path in (f'{module_dir}/index{ext}', f'{module_dir}{ext}'))


def find_users(removed, resolved):
    """Файлы, которые импортируют удалённое имя из исходного модуля: {имя: [файл]}.

    Конфликтный файл читаем из черновика результата, а не из рабочей копии
    с маркерами. Тёзка из другого модуля потребителем не считается.
    """
    @functools.lru_cache(maxsize=None)
    def text_of(path):
        if path in resolved:
            return decode(resolved[path]) or ''
        try:
            return Path(path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None

    @functools.lru_cache(maxsize=None)
    def exports_of(path):
        return exported_names(path, text_of(path))

    if not removed:
        return {}
    # Один проход `git grep` на все имена: `-o -z` печатает `файл\0имя` на каждое вхождение.
    out = git('grep', '-I', '-o', '-z', '-w', '-F',
              *[arg for name in removed for arg in ('-e', name)],
              '--', *[f'*{ext}' for ext in CODE_EXT])
    grep_hits = {}
    for line in out.splitlines():
        path, _, name = line.partition('\0')
        grep_hits.setdefault(name, {})[path] = None

    users = {}
    for name, sources in removed.items():
        own = {p for p, _ in sources}
        hits = [f for f in grep_hits.get(name, ()) if f not in own
                and any(target is None or not reexports(target, name, exports_of)
                        for src in own
                        for target in import_targets(name, f, src, text_of(f) or ''))]
        if hits:
            users[name] = hits
    return users


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', help='куда положить списки и черновики')
    args = parser.parse_args()

    ref = theirs_ref()
    if not ref:
        print('Нет MERGE_HEAD / CHERRY_PICK_HEAD / REBASE_HEAD / REVERT_HEAD: история theirs')
        print('недоступна (stash pop/apply). Классификация не применима — разбирай вручную.')
        return 0

    files = git('diff', '--name-only', '--diff-filter=U').splitlines()
    if not files:
        print('Unmerged-файлов нет.')
        return 0

    out = args.out or tempfile.mkdtemp(prefix='merge-resolve-')
    Path(out).mkdir(parents=True, exist_ok=True)

    groups = {cls: [] for cls in CLASSES}
    reasons = {}
    results = []
    revert_list = find_reverts(ref)
    # Путь, который трогали несколько откатов, получает самый новый.
    reverts = {}
    for revert in revert_list:
        for path in revert.paths:
            reverts.setdefault(path, revert)
    sides = Sides(ref, files, revert_list)
    # История файла в большом репо — до секунды на сторону; git-процессы
    # независимы, поэтому файлы идут параллельно.
    with ThreadPoolExecutor(max_workers=8) as pool:
        classified = list(pool.map(lambda p: classify(p, ref, reverts, sides), files))

    for path, (cls, reason, result, draft) in zip(files, classified):
        groups[cls].append(path)
        if draft is not None:
            write_draft(out, path, draft)
        if reason:
            reasons[path] = reason
        if result is not None:
            results.append((path, cls, result))

    for cls, paths in groups.items():
        Path(out, f'{cls}.txt').write_text(''.join(f'{p}\n' for p in paths))
    Path(out, 'reasons.txt').write_text(
        ''.join(f'{p}\t{reasons[p]}\n' for p in groups['manual']))
    Path(out, 'side-commits.txt').write_text(''.join(
        f'{p}\t{side}\t{sha[:10]} {subject}\n'
        for p in groups['manual'] for side, sha, subject in sides.uncovered(p)))

    print(f'ОПЕРАЦИЯ: theirs = {ref}, ours = HEAD (при rebase стороны инвертированы)')
    print(f'КЛАССИФИКАЦИЯ ({len(files)} файлов). Списки и черновики: {out}')
    for cls, paths in groups.items():
        removed = 0
        if cls == 'both-deleted':
            removed = len(paths)
        elif cls in KEEP_STAGE:
            removed = sum(1 for p in paths if not stage_blob(KEEP_STAGE[cls], p))
        tail = f'  (git rm: {removed})' if removed else ''
        print(f'  {cls:<15} {len(paths):4}  {cls}.txt{tail}')

    if groups['manual']:
        kinds = Counter(reason_kind(reasons[p]) for p in groups['manual'])
        print('\nРУЧНЫЕ по причинам (по файлу — reasons.txt; коммиты сторон вне squash — '
              'side-commits.txt):')
        for kind, count in kinds.most_common():
            print(f'  {count:4}  {kind}')

    conflicted = set(files)
    shown = [r for r in revert_list if conflicted.intersection(r.paths)]
    if shown:
        print('\nОТКАТЫ (развилка до плана: принять откат или сохранить откатанное — '
              'revert of revert):')
        for r in shown:
            print(f'  {r.side} {r.sha[:8]} «{r.subject}» — файлов: {len(r.paths)}, '
                  f'в конфликте: {len(conflicted.intersection(r.paths))}')

    # Авто-слитые файлы тоже меняют контракт: git убрал реэкспорт из индекса
    # одной стороны, а потребитель другой стороны его импортирует.
    blobs = BlobReader()
    for path in git('diff', '--cached', '--name-only', '--diff-filter=M', 'HEAD').splitlines():
        if path.endswith(CODE_EXT) and path not in files:
            data = blobs.read(f':0:{path}')
            if data is not None:
                results.append((path, 'auto', data))

    removed = removed_exports(results, ref, blobs)
    blobs.close()
    users = find_users(removed, {path: result for path, _, result in results})
    if users:
        print('\nУДАЛЁННЫЕ ЭКСПОРТЫ С ПОТРЕБИТЕЛЯМИ (результат убрал символ, а файлы импортируют его оттуда):')
        for name in sorted(users):
            src = ', '.join(f'{p} [{c}]' for p, c in removed[name])
            hits = users[name]
            more = f' (+{len(hits) - MAX_FILES_SHOWN})' if len(hits) > MAX_FILES_SHOWN else ''
            print(f'  {name} — убран из {src}; встречается: '
                  f'{", ".join(hits[:MAX_FILES_SHOWN])}{more}')
        print('Каждая строка — развилка для плана: потребитель другой стороны держит старый '
              'контракт. [auto] — файл слил git, без конфликта.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
