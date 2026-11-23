#!/usr/bin/env python3
"""Самопроверка скриптов merge-resolve на временных git-репо.

Запуск: python3 scripts/test_merge_scripts.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
fails = []

LINES = ['line1', 'line2', 'line3', 'line4', 'line5', 'line6', 'line7']


def git(*args, check=True):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          check=check, capture_output=True, text=True).stdout


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)


def lines(**changes):
    return ''.join(f'{changes.get(line, line)}\n' for line in LINES)


def commit(msg):
    git('add', '-A')
    git('commit', '-q', '-m', msg)


def run(script, *args):
    r = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True)
    return r.stdout + r.stderr


def expect(cond, msg):
    if not cond:
        fails.append(msg)


ENTITY_V1 = 'export enum Mode { A }\nexport const keep = 1;\n'
ENTITY_V2 = 'export enum Mode { A, B }\nexport const keep = 1;\n'
ENTITY_V3 = 'export const keep = 1;\n'

# Сборочная ветка повторяет версии master независимыми коммитами — как снимок
# фичи, принесённый в сборку другим мержем. Для git это конфликт от базы, хотя
# своих правок у сборки в older.ts и entity.ts нет.
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    write('older.ts', 'export const a = 1;\n')
    write('both.ts', lines())
    write('clash.ts', 'export const x = 1;\n')
    write('entity.ts', ENTITY_V1)
    write('slice/ui/types.ts', 'export type Price = number;\nexport const other = 1;\n')
    write('slice/index.ts', "export type { Price } from './ui/types';\n")
    write('dup.ts', lines())
    commit('base')

    git('checkout', '-q', '-b', 'build')
    write('older.ts', 'export const a = 2;\n')
    write('both.ts', lines(line1='line1 shared'))
    write('dup.ts', lines(line1='line1 shared'))
    commit('build takes feature')
    write('both.ts', lines(line1='line1 shared', line2='line2 build'))
    write('dup.ts', lines(line1='line1 shared', line2="import { b } from './y';"))
    write('clash.ts', 'export const x = 4;\n')
    write('entity.ts', ENTITY_V2)
    write('consumer.ts', "import { Mode } from './entity';\n")
    write('price-consumer.ts', "import { type Price } from './slice';\n")
    commit('build snapshot')

    git('checkout', '-q', 'main')
    write('older.ts', 'export const a = 2;\n')
    write('both.ts', lines(line1='line1 shared'))
    write('dup.ts', lines(line1='line1 shared'))
    write('entity.ts', ENTITY_V2)
    commit('feature lands')
    write('older.ts', 'export const a = 3;\n')
    write('both.ts', lines(line1='line1 shared', line7='line7 theirs'))
    write('dup.ts', lines(line1='line1 shared', line7="import { b } from './z';"))
    write('clash.ts', 'export const x = 3;\n')
    write('entity.ts', ENTITY_V3)
    # Price переезжает внутри слайса: индекс реэкспортирует его из нового файла.
    write('slice/ui/types.ts', 'export const other = 1;\n')
    write('slice/model/price.ts', 'export type Price = number;\n')
    write('slice/index.ts', "export type { Price } from './model/price';\n")
    commit('master moves on')

    git('checkout', '-q', 'build')
    git('merge', '-q', 'main', check=False)

    out = run('classify_conflicts.py', '--out', os.path.join(repo, '.out'))
    lists = {name: Path(repo, '.out', f'{name}.txt').read_text().split()
             for name in ('take-theirs', 'take-ours', 'realbase-clean', 'manual')}
    expect(lists['take-theirs'] == ['entity.ts', 'older.ts'], f'take-theirs\n{out}')
    expect(lists['realbase-clean'] == ['both.ts'], f'realbase-clean\n{out}')
    expect(lists['manual'] == ['clash.ts', 'dup.ts'], f'manual\n{out}')
    expect('dup.ts\tчерновик от реальной базы задваивает имена «import b»'
           in Path(repo, '.out/reasons.txt').read_text(), f'дубль импорта в черновике\n{out}')
    expect(Path(repo, '.out/drafts/both.ts').read_text()
           == lines(line1='line1 shared', line2='line2 build', line7='line7 theirs'),
           'черновик both.ts')
    expect('Mode — убран из entity.ts [take-theirs]' in out and 'consumer.ts' in out,
           f'удалённый экспорт Mode с потребителем consumer.ts\n{out}')
    expect('Price' not in out, f'Price по-прежнему реэкспортирует slice/index.ts\n{out}')

    git('merge', '--abort')

# Откат: master откатывает фичу, а сборка поверх фичи правила тот же файл.
# Версия theirs (до фичи) есть в истории ours, но take-ours здесь оставил бы
# фичу в файле, а take-theirs выкинул бы правку сборки.
FEAT_TAIL = 'export const feat = () => {};\n'
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    write('f.ts', lines())
    commit('base')
    write('f.ts', lines(line1='feat') + FEAT_TAIL)
    commit('feature')

    git('checkout', '-q', '-b', 'build')
    write('f.ts', lines(line1='feat build') + FEAT_TAIL)
    commit('build edits the feature')

    git('checkout', '-q', 'main')
    git('revert', '--no-edit', 'HEAD')
    revert_sha = git('rev-parse', 'HEAD').strip()

    git('checkout', '-q', 'build')
    git('merge', '-q', 'main', check=False)

    out = run('classify_conflicts.py', '--out', os.path.join(repo, '.out'))
    lists = {name: Path(repo, '.out', f'{name}.txt').read_text().split()
             for name in ('take-ours', 'manual')}
    expect(lists['take-ours'] == [] and lists['manual'] == ['f.ts'], f'откат — manual\n{out}')
    expect(f'theirs {revert_sha[:8]} «Revert "feature"» — файлов: 1, в конфликте: 1' in out,
           f'блок ОТКАТЫ\n{out}')
    expect(f'черновик принимает откат (база {revert_sha[:8]}^)'
           in Path(repo, '.out/reasons.txt').read_text(), f'причина manual\n{out}')
    draft_path = Path(repo, '.out/drafts/f.ts')
    draft = draft_path.read_text() if draft_path.exists() else ''
    expect('feat = ' not in draft and '||||||| ' in draft,
           f'черновик от версии до отката: хвост фичи снят, line1 в конфликте\n{draft}')

    out = run('check_merge_symbols.py')
    expect(f'ПРОПАЛ   feat — есть в ours, в результате нет; менял коммит theirs {revert_sha[:8]}'
           in out, f'пропажа feat с коммитом отката\n{out}')

    git('merge', '--abort')

# check_merge_symbols: многострочные сигнатуры и файл-перемотка. Оба файла меняют
# обе стороны, иначе merge_scope их отсеет и проверять будет нечего.
A_TAIL = '// t1\n// t2\n'
B_STAY = 'export const stay = 1;\n// t1\n// t2\n'
FIELDS = 'interface P {\n  a: string;\n  b: string;\n  c: string;\n  d: string;\n}\n'
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    write('a.ts', 'class A {\n  _toggle(name: string) {}\n}\n'
                  'export const enter = async (x: number) => {};\n' + A_TAIL)
    write('b.ts', 'export const gone = () => {};\n' + B_STAY)
    write('p.ts', FIELDS)
    commit('base')

    git('checkout', '-q', '-b', 'side')
    write('a.ts', 'class A {\n  _toggle(\n    name: string,\n  ): void {}\n}\n'
                  'export const enter = async (\n  x: number,\n): Promise<void> => {};\n' + A_TAIL)
    write('b.ts', 'export const gone = () => {};\n' + B_STAY + '// v1\n')
    write('p.ts', FIELDS.replace('  a: string;\n', '  a: string;\n  label: string;\n'))
    commit('reformat, take b v1')

    git('checkout', '-q', 'main')
    write('b.ts', 'export const gone = () => {};\n' + B_STAY + '// v1\n')
    write('p.ts', FIELDS.replace('  d: string;\n', '  d: string;\n  label?: string;\n'))
    commit('take b v1')
    write('a.ts', Path('a.ts').read_text() + 'export const z = 1;\n')
    write('b.ts', B_STAY + '// v1\n')
    commit('remove gone')

    git('checkout', '-q', 'side')
    git('merge', '-q', '-m', 'merge main', 'main')

    out = run('check_merge_symbols.py')
    expect('СТРУКТУРНАЯ СВЕРКА СИМВОЛОВ (3 файлов)' in out,
           f'правленые с двух сторон файлы в скоупе\n{out}')
    expect('_toggle' not in out and 'enter' not in out,
           f'многострочные сигнатуры не считаются пропавшими\n{out}')
    expect('gone' not in out, f'пропажа в файле-перемотке не показывается\n{out}')
    expect('ДУБЛЬ    поле label — 2 раза вне маркеров' in out,
           f'авто-мерж задвоил поле interface\n{out}')

    Path(repo, 'only.txt').write_text('a.ts\n')
    out = run('check_merge_symbols.py', '--files', 'only.txt')
    expect('СТРУКТУРНАЯ СВЕРКА СИМВОЛОВ (1 файлов)' in out and 'label' not in out,
           f'--files сужает проверку\n{out}')


def squash_merge(branch, files, review):
    """Squash в стиле GitLab: один коммит с итогом ветки и правкой ревью, потом merge-коммит."""
    git('checkout', '-q', '-b', f'squash-{branch}', 'main')
    for path, text in files.items():
        if text is None:
            git('rm', '-q', path)
        else:
            write(path, text)
    write('review.txt', review)
    commit(f'{branch} (squash)')
    git('checkout', '-q', 'main')
    git('merge', '-q', '--no-ff', '-m', f"Merge branch '{branch}' into 'main'", f'squash-{branch}')
    git('branch', '-q', '-D', f'squash-{branch}')


# Squash: сборка влила ветки фичи как есть, master — их squash после ревью.
# Коммиты сборки по файлу лежат в влитой master'ом ветке — версия сборки старая.
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    for name in ('w.ts', 'x.ts', 'd.ts', 'g.ts'):
        write(name, lines())
    commit('base')

    git('checkout', '-q', '-b', 'feature/F')
    write('w.ts', lines(line2='w feature'))
    write('x.ts', lines(line2='x feature'))
    write('d.ts', lines(line2='d feature'))
    commit('F: first')
    write('w.ts', lines(line2='w feature', line3='w feature 2'))
    commit('F: second')

    git('checkout', '-q', '-b', 'feature/G', 'main')
    write('g.ts', lines(line2='g feature'))
    commit('G: work')

    git('checkout', '-q', '-b', 'build', 'main')
    git('checkout', '-q', 'main')
    write('prelude.txt', 'main moves\n')
    commit('main prelude')
    old_main = git('rev-parse', 'HEAD').strip()
    git('checkout', '-q', 'build')
    git('merge', '-q', '--no-ff', '-m', 'Merge branch feature/F into build', 'feature/F')
    git('merge', '-q', '--no-ff', '-m', 'Merge branch feature/G into build', 'feature/G')
    write('x.ts', lines(line2='x feature', line6='x build only'))
    commit('build: own fix in x')

    git('checkout', '-q', 'main')
    squash_merge('feature/F', {'w.ts': lines(line2='w reviewed', line3='w feature 2'),
                               'x.ts': lines(line2='x reviewed'), 'd.ts': None}, 'F')
    squash_merge('feature/G', {'g.ts': lines(line2='g reviewed')}, 'G')
    git('revert', '--no-edit', '-m', '1', 'HEAD')
    write('g.ts', lines(line2='g main later'))
    commit('main edits g')

    git('checkout', '-q', 'build')
    # Обычный мерж старого снимка main уже после squash: по имени ветки и
    # времени он похож на покрытие, но squash-коммитов main не приносит.
    git('merge', '-q', '--no-ff', '-m', "Merge branch 'main' into 'build'", old_main)
    git('merge', '-q', 'main', check=False)

    out = run('classify_conflicts.py', '--out', os.path.join(repo, '.out'))
    lists = {name: Path(repo, '.out', f'{name}.txt').read_text().split()
             for name in ('squash-theirs', 'manual')}
    expect(lists['squash-theirs'] == ['d.ts', 'w.ts'], f'squash-theirs\n{out}')
    expect('squash-ours        0' in out, f'обычный мерж main в сборку не покрывает\n{out}')
    expect('squash-theirs      2  squash-theirs.txt  (git rm: 1)' in out,
           f'd.ts удалён в master: git rm в сводке\n{out}')
    expect(set(lists['manual']) == {'g.ts', 'x.ts'},
           f'x.ts — своя правка сборки, g.ts — мерж G откатили\n{out}')
    side = Path(repo, '.out/side-commits.txt').read_text()
    expect('x.ts\tours\t' in side and 'build: own fix in x' in side
           and 'F: first' not in side, f'side-commits: только коммит вне squash\n{side}')
    expect('G: work' in side, f'откаченный мерж G не покрывает его коммиты\n{side}')
    git('merge', '--abort')

# Ренейм на обеих сторонах в разные имена: старый путь остаётся только в базе.
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    write('r.ts', lines())
    commit('base')
    git('checkout', '-q', '-b', 'build')
    git('mv', 'r.ts', 'r1.ts')
    commit('rename to r1')
    git('checkout', '-q', 'main')
    git('mv', 'r.ts', 'r2.ts')
    commit('rename to r2')
    git('checkout', '-q', 'build')
    git('merge', '-q', 'main', check=False)

    out = run('classify_conflicts.py', '--out', os.path.join(repo, '.out'))
    both = Path(repo, '.out/both-deleted.txt').read_text().split()
    expect(both == ['r.ts'], f'r.ts — both-deleted\n{out}')
    expect('both-deleted       1  both-deleted.txt  (git rm: 1)' in out, f'сводка both-deleted\n{out}')
    git('merge', '--abort')

# Сторона новее только через merge-коммит — не перемотка, а manual.
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    write('m.ts', lines())
    commit('base')
    git('checkout', '-q', '-b', 'build')
    write('m.ts', lines(line1='ours'))
    commit('ours')
    git('checkout', '-q', 'main')
    write('m.ts', lines(line1='theirs'))
    commit('theirs')
    git('checkout', '-q', 'build')
    git('merge', '-q', 'main', check=False)

    sys.path.insert(0, str(HERE))
    import classify_conflicts as cc

    class NoOwnCommits:
        ours_commits, theirs_commits = {}, {}

        @staticmethod
        def squash_side(_path):
            return None

    real_forward = cc.fast_forward_side
    cc.fast_forward_side = lambda *_args: 'ours'
    cls, reason, _, _ = cc.classify('m.ts', 'MERGE_HEAD', {}, NoOwnCommits())
    cc.fast_forward_side = real_forward
    expect((cls, reason) == ('manual', 'ours новее только через merge-коммиты'),
           f'перемотка без своих коммитов: {cls} {reason}')
    git('merge', '--abort')

# Сирота: файл ours в каталоге, который целиком взяли с theirs.
with tempfile.TemporaryDirectory() as repo:
    os.chdir(repo)
    git('init', '-q', '-b', 'main')
    write('w/a.ts', lines())
    commit('base')
    git('checkout', '-q', '-b', 'build')
    write('w/orphan.ts', 'export const lost = 1;\n')
    write('w/orphan.test.ts', "import { lost } from './orphan';\n")
    write('w/used.ts', 'export const used = 1;\n')
    write('other.ts', "import { used } from './w/used';\n")
    commit('ours adds files')
    git('checkout', '-q', 'main')
    write('w/a.ts', lines(line1='theirs'))
    commit('theirs edits a')
    git('checkout', '-q', 'build')
    git('merge', '-q', '-m', 'merge main', 'main')

    out = run('find_orphans.py', '--side', 'theirs', 'w')
    expect('w/orphan.ts — ссылок нет: сирота' in out, f'сирота со своим тестом\n{out}')
    expect('w/used.ts — ссылки: other.ts' in out, f'файл со ссылкой\n{out}')

if fails:
    print('FAIL:\n' + '\n---\n'.join(fails))
    sys.exit(1)
print('OK')
