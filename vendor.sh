#!/bin/bash
# ============================================================================
#  Scripture Browser — vendor the browser.
# ============================================================================
#
#  Takes a copy of the machine's Firefox and makes it this browser's own.  Run
#  by install.sh as root; `vendor.sh --force` re-vendors even when the clone is
#  already up to date.
#
#  The copy is made with hard links where the filesystem allows it (`cp -al`),
#  which costs no disk and no time: the clone gets its own directory tree, while
#  the bytes of libxul.so stay shared with the copy the package manager keeps.
#  When the package manager replaces Firefox it writes *new* files, so the clone
#  keeps the old ones intact and simply goes stale — until `install.sh --sync`
#  re-vendors it from the new ones.
#
#  Nothing here ever writes through a shared file.  The overrides —
#  application.ini, the autoconfig, the policies — are added afterwards by
#  install.sh, and application.ini is *replaced* rather than edited, because
#  build.py generates the patched one from the machine's own copy.
# ============================================================================

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

say()  { printf '%s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf 'vendor.sh: %s\n' "$*" >&2; exit 1; }

build_id() { [ -f "$1/platform.ini" ] && sed -n 's/^BuildID=//p' "$1/platform.ini" | head -1; }

[ "$(id -u)" -eq 0 ] || die "run this with sudo"
[ -f "$HERE/config.json" ] || die "config.json is missing — run this from the project directory"

PREFIX=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["install_prefix"].rstrip("/"))' \
         "$HERE/config.json")
CLONE="$PREFIX/lib/firefox"

SOURCE=$(python3 "$HERE/build.py" --print-firefox-dir)
[ -n "$SOURCE" ] || die "no Firefox installation found on this machine to clone"
[ -d "$SOURCE" ] || die "$SOURCE is not a directory"

new_id=$(build_id "$SOURCE" || true)
old_id=$(build_id "$CLONE" || true)

if [ -n "$old_id" ] && [ "$old_id" = "$new_id" ] && [ -f "$CLONE/application.ini" ] \
   && [ "$FORCE" != 1 ]; then
  say "  clone is current (Firefox $new_id)"
  exit 0
fi

# Swapping the directory under a running browser is not worth the risk.  The
# timer and the path unit will try again; the old clone keeps working meanwhile.
if pgrep -f "$CLONE/firefox" >/dev/null 2>&1; then
  if [ "$FORCE" = 1 ]; then
    warn "the browser is running from $CLONE — close it and reopen after this"
  else
    say "  deferred: the browser is running from the clone; will re-vendor next time"
    exit 0
  fi
fi

# Our own files inside the old clone are frozen; they have to be openable to be
# moved aside.  Nothing else is frozen, and nothing else is touched.
if [ -x /usr/local/lib/scripture-browser-guard/guard.sh ]; then
  /usr/local/lib/scripture-browser-guard/guard.sh unfreeze >/dev/null 2>&1 || true
fi

install -d -m 755 "$PREFIX/lib"
rm -rf "$PREFIX/lib/.firefox.new" "$PREFIX/lib/.firefox.old"

how="hard links — no extra disk"
if ! cp -al "$SOURCE" "$PREFIX/lib/.firefox.new" 2>/dev/null; then
  # Different filesystem, or a kernel that will not let root link another user's
  # files.  A real copy costs about 300 MB and is the honest fallback.
  rm -rf "$PREFIX/lib/.firefox.new"
  cp -a "$SOURCE" "$PREFIX/lib/.firefox.new"
  how="a full copy — about 300 MB"
fi

[ -f "$PREFIX/lib/.firefox.new/application.ini" ] \
  || die "the copy is incomplete: $PREFIX/lib/.firefox.new has no application.ini"

# Swap, so that a failed copy leaves a working clone rather than half of one.
if [ -d "$CLONE" ]; then
  mv "$CLONE" "$PREFIX/lib/.firefox.old"
fi
mv "$PREFIX/lib/.firefox.new" "$CLONE"
rm -rf "$PREFIX/lib/.firefox.old"

say "  vendored Firefox $new_id from $SOURCE"
say "  $how"
