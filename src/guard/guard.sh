#!/bin/bash
# ============================================================================
#  Scripture Browser — the guard.
# ============================================================================
#
#  The browser's locks are a set of files in known places.  This puts them back.
#  It reads a manifest of what should be where (and the sha256 of each), compares
#  what is on disk, and copies the sealed original over anything that differs or
#  has gone missing.  Then it makes those files immutable — `chattr +i`, which
#  even root has to undo on purpose before it can delete them.
#
#     guard.sh restore     put back what is missing or changed, and re-freeze
#     guard.sh status      say what is intact and what is not (exit 1 if not)
#     guard.sh freeze      make the managed files immutable
#     guard.sh unfreeze    let an install through
#     guard.sh seal TREE M take the sealed copy of TREE, and adopt manifest M
#     guard.sh unseal      give up the sealed copy and the immutability
#
#  The manifest has one row per managed file:
#
#     destination<TAB>mode<TAB>freeze<TAB>sha256<TAB>path-in-sealed
#
#  Two paths rather than one because two of our files live inside Firefox's own
#  directory, which is only found on the machine being installed to.  sha256 is
#  the hash of the sealed copy, and is the last word on what the file should be.
#
#  It is deliberately small and boring.  It runs from systemd, as root, on a
#  timer and whenever one of the files is touched.  It never deletes anything it
#  does not own, and it does nothing at all when everything matches — which is
#  what keeps it from tripping its own trigger.
#
#  SCRIPTURE_GUARD_ROOT exists so the logic can be tested against a scratch
#  directory without touching a real system.  Nothing sets it in production.
# ============================================================================

set -u

GUARD_DIR=$(cd "$(dirname "$0")" && pwd)
MANIFEST="$GUARD_DIR/MANIFEST"
SEALED="$GUARD_DIR/sealed"
ROOT=${SCRIPTURE_GUARD_ROOT:-}

TAG=scripture-browser-guard
quiet=0

log()  { logger -t "$TAG" -- "$*" 2>/dev/null || true; [ "$quiet" = 1 ] || printf '%s\n' "$*"; }
warn() { printf '%s: %s\n' "$TAG" "$*" >&2; logger -t "$TAG" -- "WARNING: $*" 2>/dev/null || true; }

rows() { grep -v '^[[:space:]]*#' "$MANIFEST" 2>/dev/null | grep -v '^[[:space:]]*$'; }

# A destination from the manifest, under $ROOT (which is empty in production).
# Two of our files live inside Firefox's own directory, so a destination may be
# absolute; the rest are relative and belong at the root of the filesystem.
path_for() {
  case "$1" in
    /*) printf '%s%s\n' "$ROOT" "$1" ;;
    *)  printf '%s/%s\n' "$ROOT" "$1" ;;
  esac
}

# Some filesystems have no immutable attribute at all.  Saying so once is more
# use than reporting eighty files as unfrozen on every run.
immutable_supported() { [ -e "$1" ] && lsattr -d "$1" >/dev/null 2>&1; }

# Firefox reads every *.js in its defaults/pref directory, in alphabetical order,
# and the last file to set general.config.filename wins.  A file dropped in there
# by root could point Firefox at an autoconfig with no locks in it at all.  We
# cannot prune a directory Firefox also owns — but we can refuse to be quiet.
pref_dir_from_manifest() {
  local rel mode freeze sha sealed_rel
  while IFS=$'\t' read -r rel mode freeze sha sealed_rel; do
    case "$rel" in
      */defaults/pref/autoconfig.js)
        dirname "$(path_for "$rel")"; return 0 ;;
    esac
  done < <(rows)
  return 1
}

stray_pref_files() {
  local dir f base
  dir=$(pref_dir_from_manifest) || return 0
  [ -d "$dir" ] || return 0
  for f in "$dir"/*.js; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    [ "$base" = autoconfig.js ] && continue
    [ "$base" = channel-prefs.js ] && continue
    printf '%s\n' "$f"
  done
}

# Is $1 immutable already?  (Setting +i on a file that has it is harmless, but
# asking first keeps the log honest and the inode untouched.)
is_frozen() { lsattr -d "$1" 2>/dev/null | awk '{print $1}' | grep -q 'i'; }

freeze_one() {
  local dest="$1" links
  [ "$IMMUTABLE" = 1 ] || return 0
  [ -e "$dest" ] || return 0
  is_frozen "$dest" && return 0
  # The clone shares most of its bytes with the machine's own Firefox, by hard
  # link.  Freezing one of those would freeze the package manager's copy too, and
  # the next security update would fail to unpack.  Refuse, loudly.
  links=$(stat -c %h "$dest" 2>/dev/null || echo 1)
  if [ "$links" -gt 1 ]; then
    warn "not freezing $dest: it has $links hard links — it is shared with the machine's own Firefox"
    return 0
  fi
  chattr +i "$dest" 2>/dev/null || { IMMUTABLE=0; warn "this filesystem does not support chattr +i — the guard will keep restoring, but will not block deletion"; }
}

unfreeze_one() {
  local dest="$1"
  [ -e "$dest" ] || return 0
  is_frozen "$dest" || return 0
  chattr -i "$dest" 2>/dev/null || true
}

IMMUTABLE=1

# ---------------------------------------------------------------- restore ---
cmd_restore() {
  local rel mode freeze sha sealed_rel dest src want have restored=0 failed=0 checked=0

  if [ ! -f "$MANIFEST" ]; then
    warn "no manifest at $MANIFEST — there is nothing to restore from"
    return 1
  fi
  if [ ! -d "$SEALED" ]; then
    warn "no sealed copy at $SEALED — the guard cannot put anything back"
    return 1
  fi

  while IFS=$'\t' read -r rel mode freeze sha sealed_rel; do
    [ -n "$rel" ] || continue
    checked=$((checked + 1))
    dest=$(path_for "$rel")
    src="$SEALED/${sealed_rel:-$rel}"

    if [ ! -f "$src" ]; then
      warn "the sealed copy of $rel is gone"
      failed=$((failed + 1))
      continue
    fi
    want=$(sha256sum "$src" | cut -d' ' -f1)
    have=""
    [ -f "$dest" ] && have=$(sha256sum "$dest" | cut -d' ' -f1)

    if [ "$have" != "$want" ]; then
      unfreeze_one "$dest"
      if install -D -m "$mode" "$src" "$dest" 2>/dev/null; then
        log "restored $dest"
        restored=$((restored + 1))
      else
        warn "could not write $dest"
        failed=$((failed + 1))
        continue
      fi
    fi

    if [ "$freeze" = 1 ]; then
      freeze_one "$dest"
    fi
  done < <(rows)

  local stray
  while read -r stray; do
    [ -n "$stray" ] || continue
    warn "something has been added beside our autoconfig: $stray — a prefs file that"
    warn "sorts after ours would win.  Inspect it.  (the guard will not delete it: the"
    warn "directory belongs to Firefox)"
  done < <(stray_pref_files)

  [ "$restored" -gt 0 ] && log "$restored of $checked files put back"
  [ "$failed" -gt 0 ] && { warn "$failed files could not be restored"; return 1; }
  return 0
}

# ----------------------------------------------------------------- status ---
cmd_status() {
  local rel mode freeze sha sealed_rel dest src want have bad=0 checked=0

  log "guard      $GUARD_DIR"
  if [ -f "$MANIFEST" ]; then
    log "manifest   $(grep -vc '^[[:space:]]*#' "$MANIFEST") managed files"
  else
    warn "no manifest at $MANIFEST"
    bad=1
  fi

  while IFS=$'\t' read -r rel mode freeze sha sealed_rel; do
    [ -n "$rel" ] || continue
    checked=$((checked + 1))
    dest=$(path_for "$rel")
    src="$SEALED/${sealed_rel:-$rel}"
    if [ ! -f "$src" ]; then
      printf '  SEAL LOST  %s\n' "$rel"; bad=1; continue
    fi
    want=$(sha256sum "$src" | cut -d' ' -f1)
    have=""
    [ -f "$dest" ] && have=$(sha256sum "$dest" | cut -d' ' -f1)
    if [ ! -f "$dest" ]; then
      printf '  MISSING    %s\n' "$rel"; bad=1
    elif [ "$have" != "$want" ]; then
      printf '  CHANGED    %s\n' "$rel"; bad=1
    elif [ "$freeze" = 1 ] && immutable_supported "$dest" && ! is_frozen "$dest"; then
      printf '  UNFROZEN   %s\n' "$rel"; bad=1
    fi
  done < <(rows)

  local stray
  while read -r stray; do
    [ -n "$stray" ] || continue
    printf '  STRAY PREF %s\n' "$stray"; bad=1
  done < <(stray_pref_files)

  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    local unit state
    for unit in scripture-browser-guard.timer scripture-browser-guard.path; do
      state=$(systemctl is-active "$unit" 2>/dev/null || true)
      printf '  %-12s %s\n' "${unit##scripture-browser-}" "${state:-absent}"
    done
  fi

  if [ "$bad" = 0 ]; then
    log "everything intact — $checked files"
    return 0
  fi
  log "something is not as it should be (run: sudo scripture-browser --update)"
  return 1
}

# ----------------------------------------------------------- freeze/unfreeze -
cmd_freeze() {
  local rel mode freeze sha sealed_rel dest
  while IFS=$'\t' read -r rel mode freeze sha sealed_rel; do
    [ -n "$rel" ] || continue
    [ "$freeze" = 1 ] || continue
    dest=$(path_for "$rel")
    freeze_one "$dest"
  done < <(rows)
  # The sealed copy is made immutable too: it is the thing everything else is
  # restored from, so it should be the hardest thing on the disk to remove.
  find "$SEALED" -type d -exec chattr +i {} + 2>/dev/null || true
  [ "$IMMUTABLE" = 1 ] && log "locks are frozen"
  return 0
}

cmd_unfreeze() {
  local rel mode freeze sha sealed_rel dest
  find "$SEALED" -type d -exec chattr -i {} + 2>/dev/null || true
  while IFS=$'\t' read -r rel mode freeze sha sealed_rel; do
    [ -n "$rel" ] || continue
    dest=$(path_for "$rel")
    unfreeze_one "$dest"
  done < <(rows)
  log "locks are open for an install"
  return 0
}

# ------------------------------------------------------------------- seal ---
cmd_seal() {
  local tree="$1" manifest="$2" rel mode freeze sha sealed_rel src

  [ -d "$tree" ] || { warn "no tree at $tree"; return 1; }
  [ -f "$manifest" ] || { warn "no manifest at $manifest"; return 1; }

  cmd_unfreeze >/dev/null 2>&1 || true
  rm -rf "$SEALED"
  install -d -m 700 "$SEALED"
  install -d -m 700 "$GUARD_DIR"

  local count=0
  while IFS=$'\t' read -r rel mode freeze sha sealed_rel; do
    [ -n "$rel" ] || continue
    src="$tree/${sealed_rel:-$rel}"
    [ -f "$src" ] || { warn "manifest lists a file that is not in the tree: $rel"; return 1; }
    install -D -m "$mode" "$src" "$SEALED/${sealed_rel:-$rel}" || return 1
    count=$((count + 1))
  done < <(grep -v '^[[:space:]]*#' "$manifest")

  install -m 600 "$manifest" "$MANIFEST"
  chmod -R go-rwx "$SEALED" "$MANIFEST" 2>/dev/null || true
  log "sealed $count files"
  cmd_freeze
}

cmd_unseal() {
  find "$SEALED" -type d -exec chattr -i {} + 2>/dev/null || true
  rm -rf "$SEALED" 2>/dev/null || true
  return 0
}

# -------------------------------------------------------------------- main --
case "${1:-restore}" in
  restore|verify) quiet=0; cmd_restore ;;
  status)         quiet=1; cmd_status ;;
  freeze)         quiet=1; cmd_freeze ;;
  unfreeze)       quiet=1; cmd_unfreeze ;;
  seal)           shift; cmd_seal "${1:-}" "${2:-}" ;;
  unseal)         quiet=1; cmd_unseal ;;
  *)              printf 'usage: %s [restore|status|freeze|unfreeze|seal TREE MANIFEST|unseal]\n' \
                         "${0##*/}" >&2; exit 2 ;;
esac
