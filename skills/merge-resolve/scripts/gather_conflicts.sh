#!/usr/bin/env bash
# gather_conflicts.sh — диагностика конфликтов git одним прогоном.
# Только чтение: не трогает индекс, рабочую копию, не делает add/commit.
#
# Печатает: тип операции (merge/rebase/cherry-pick/stash) + подсказку про
# инверсию ours/theirs, список unmerged-файлов, и по каждому файлу —
# конфликтные хунки, версии base/ours/theirs из индекса и коммиты обеих
# сторон, приведшие к расхождению.
#
# Usage: bash scripts/gather_conflicts.sh

set -uo pipefail

GITDIR="$(git rev-parse --absolute-git-dir 2>/dev/null)" || {
  echo "Не git-репозиторий (или git недоступен)." >&2
  exit 1
}

hr() { printf '%s\n' "────────────────────────────────────────────────────────"; }

# ── Тип операции и смысл сторон ──────────────────────────────────────────
OURS_LABEL="HEAD (твоя ветка)"
THEIRS_LABEL="вливаемая сторона"
OP="unknown"

if [ -f "$GITDIR/MERGE_HEAD" ]; then
  OP="merge"
  echo "ОПЕРАЦИЯ: git merge"
  echo "  ours  (:2) = HEAD, твоя текущая ветка"
  echo "  theirs(:3) = MERGE_HEAD, вливаемая ветка"
elif [ -d "$GITDIR/rebase-merge" ] || [ -d "$GITDIR/rebase-apply" ]; then
  OP="rebase"
  OURS_LABEL="ветка-цель (на что перебазируемся)"
  THEIRS_LABEL="твой коммит, который накатывается"
  echo "ОПЕРАЦИЯ: git rebase"
  echo "  ⚠ ВНИМАНИЕ: стороны ИНВЕРТИРОВАНЫ относительно merge!"
  echo "  ours  (:2) = ветка-ЦЕЛЬ (на что перебазируешься), НЕ твоя работа"
  echo "  theirs(:3) = ТВОЙ коммит, который сейчас накатывается"
elif [ -f "$GITDIR/CHERRY_PICK_HEAD" ]; then
  OP="cherry-pick"
  OURS_LABEL="текущая ветка"
  THEIRS_LABEL="переносимый коммит"
  echo "ОПЕРАЦИЯ: git cherry-pick"
  echo "  ⚠ ВНИМАНИЕ: theirs — это ПЕРЕНОСИМЫЙ коммит, ours — текущая ветка."
  echo "  ours  (:2) = текущая ветка"
  echo "  theirs(:3) = переносимый коммит (CHERRY_PICK_HEAD)"
else
  OP="stash-or-plain"
  echo "ОПЕРАЦИЯ: нет активного merge/rebase/cherry-pick."
  echo "  Вероятно конфликт от 'git stash pop/apply' (или ручной)."
  echo "  ours  (:2) = текущее состояние ветки (HEAD/индекс)"
  echo "  theirs(:3) = содержимое из stash"
fi
hr

# ── Список конфликтных файлов ────────────────────────────────────────────
FILES=()
while IFS= read -r line; do FILES+=("$line"); done \
  < <(git diff --name-only --diff-filter=U 2>/dev/null)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "Unmerged-файлов нет (git diff --diff-filter=U пуст)."
  echo "Если ждал конфликты — проверь 'git status' вручную: возможно операция уже"
  echo "завершена, либо конфликт в untracked/добавленных файлах."
  exit 0
fi

echo "КОНФЛИКТНЫЕ ФАЙЛЫ (${#FILES[@]}):"
for f in "${FILES[@]}"; do echo "  • $f"; done
hr

# ── Источники heads для лога коммитов ────────────────────────────────────
# ours-ref / theirs-ref — откуда тянуть коммиты каждой стороны.
case "$OP" in
  merge)
    OURS_REF="HEAD"
    THEIRS_REF="MERGE_HEAD"
    ;;
  cherry-pick)
    OURS_REF="HEAD"
    THEIRS_REF="CHERRY_PICK_HEAD"
    ;;
  rebase)
    # При rebase HEAD = ветка-цель (ours). theirs-коммит надёжно не вычислить
    # из файлов состояния во всех версиях git — лог theirs пропускаем.
    OURS_REF="HEAD"
    THEIRS_REF=""
    ;;
  *)
    OURS_REF="HEAD"
    THEIRS_REF=""
    ;;
esac

# merge-base для диапазонов лога (если обе ref-ы доступны)
BASE=""
if [ -n "$THEIRS_REF" ] && git rev-parse --verify -q "$THEIRS_REF" >/dev/null; then
  BASE="$(git merge-base "$OURS_REF" "$THEIRS_REF" 2>/dev/null || true)"
fi

# ── Детали по каждому файлу ──────────────────────────────────────────────
for f in "${FILES[@]}"; do
  hr
  echo "ФАЙЛ: $f"
  hr

  echo "── Конфликтные маркеры (номера строк) ──"
  grep -nE '^(<<<<<<<|\|\|\|\|\|\|\||=======|>>>>>>>)' "$f" 2>/dev/null \
    || echo "  (маркеров не найдено — возможно add/add или delete/modify конфликт)"
  echo

  echo "── Намерение сторон ($OURS_LABEL ↔ $THEIRS_LABEL) ──"
  if [ -n "$BASE" ]; then
    echo "Коммиты OURS [$OURS_LABEL] ($BASE..$OURS_REF):"
    git log --oneline "$BASE..$OURS_REF" -- "$f" 2>/dev/null | sed 's/^/  /' || true
    echo "Коммиты THEIRS [$THEIRS_LABEL] ($BASE..$THEIRS_REF):"
    git log --oneline "$BASE..$THEIRS_REF" -- "$f" 2>/dev/null | sed 's/^/  /' || true
  else
    echo "  merge-base недоступен (rebase/stash). История по файлу:"
    git log --oneline -10 -- "$f" 2>/dev/null | sed 's/^/  /' || true
  fi
  echo

  echo "── git diff (объединённый, показывает обе стороны) ──"
  diff_out="$(git diff -- "$f" 2>/dev/null)"
  printf '%s\n' "$diff_out" | head -200
  [ "$(printf '%s\n' "$diff_out" | wc -l)" -gt 200 ] \
    && echo "  …diff обрезан до 200 строк — полностью: git diff -- $f"
  echo

  echo "Подсказка: полные версии сторон из индекса —"
  echo "  base  : git show :1:$f"
  echo "  ours  : git show :2:$f   # $OURS_LABEL"
  echo "  theirs: git show :3:$f   # $THEIRS_LABEL"
done
hr
echo "Готово. Дальше: разобрать намерение коммитов, при сомнении — спросить,"
echo "собрать единый план разрешений, аппрув, правки, верификация."
