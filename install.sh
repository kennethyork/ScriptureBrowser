#!/bin/bash
# ============================================================================
#  Scripture Browser — install, update, sync, and the way out.
# ============================================================================
#
#     sudo ./install.sh                 install it, or rebuild and reinstall
#     sudo ./install.sh --update        the same, after editing allowlist.txt
#     sudo ./install.sh --sync          re-vendor the clone if the machine's
#                                       Firefox has been upgraded
#     sudo ./install.sh --home-server   serve the start page on loopback, for a
#                                       browser that will not open file:// URLs
#     sudo ./install.sh --release       remove everything this script installed
#     sudo ./install.sh --no-guard      debug: install without the guardian
#
#  In order:
#
#     1. builds build/tree from allowlist.txt and config.json   (build.py)
#     2. opens the locks, so that new files can be written at all
#     3. vendors the browser: a copy of the machine's Firefox, hard-linked where
#        the filesystem allows it, into <prefix>/lib/firefox    (vendor.sh)
#     4. copies the tree over it — the clone's four own files, the filter, the
#        start page, the launcher, the units
#     5. adopts the manifest, and seals a copy of every file it names
#     6. turns on the filter, the guard and the sync timer
#     7. freezes the managed files with chattr +i
#
#  It does not touch the machine's own Firefox: not its directory, not its
#  profile, not its policies.  And it writes nothing in /etc at all — every file
#  belongs to the clone, or to this project's own paths.
# ============================================================================

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
BUILD="$HERE/build"
TREE="$BUILD/tree"
MANIFEST="$BUILD/MANIFEST"
GUARD_DIR=/usr/local/lib/scripture-browser-guard
GUARD="$GUARD_DIR/guard.sh"
LAUNCHER=/usr/local/bin/scripture-browser
UNITS=(guard.service guard.path guard.timer
       filter.service
       sync.service sync.path sync.timer
       home.service)

do_build=1
do_guard=1
mode=install
home_server=""
run_as_user=""

say()  { printf '%s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; }
build_id() { [ -f "$1/platform.ini" ] && sed -n 's/^BuildID=//p' "$1/platform.ini" | head -1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --update|--build)  mode=install ;;
    --sync)            mode=sync ;;
    --release|--uninstall|--remove) mode=release ;;
    --no-build)        do_build=0 ;;
    --no-guard)        do_guard=0 ;;
    --home-server)     home_server=http ;;
    --home-server-off) home_server=file ;;
    --user)            shift; run_as_user=${1:-} ;;
    -h|--help)         usage; exit 0 ;;
    *)                 printf 'unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || die "run this with sudo"
[ -f "$HERE/config.json" ] || die "config.json is missing — run this from the project directory"

PREFIX=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["install_prefix"].rstrip("/"))' \
         "$HERE/config.json")
FIREFOX_DIR=$(python3 "$HERE/build.py" --print-firefox-dir 2>/dev/null || true)

# ================================================================ release ===
if [ "$mode" = release ]; then
  say "Removing Scripture Browser…"

  # Nothing can be deleted while it is immutable or sealed, so open first.
  if [ -x "$GUARD" ]; then
    "$GUARD" unfreeze 2>/dev/null || true
    "$GUARD" unseal   2>/dev/null || true
  fi

  for unit in "${UNITS[@]}"; do
    systemctl disable --now "scripture-browser-$unit" 2>/dev/null || true
    rm -f "/usr/lib/systemd/system/scripture-browser-$unit"
  done
  systemctl daemon-reload 2>/dev/null || true
  systemctl reset-failed 2>/dev/null || true

  # Everything it installed is named in the manifest it sealed — so removal is
  # that list, rather than a second list here that can drift from it.
  removed=0
  if [ -f "$GUARD_DIR/MANIFEST" ]; then
    while IFS=$'\t' read -r d_path d_mode d_freeze d_sha d_sealed; do
      case "$d_path" in ''|'#'*) continue ;; esac
      rm -f "/$d_path" && removed=$((removed + 1))
    done < "$GUARD_DIR/MANIFEST"
    say "  $removed files removed"
  else
    warn "no manifest found — falling back to the known paths"
    rm -f /usr/local/bin/scripture-browser
    rm -f /usr/share/applications/scripture-browser.desktop
    rm -rf "$GUARD_DIR"
  fi

  # Directories it made, but only if nothing else is living in them.
  for dir in /usr/local/lib/scripture-browser-guard; do
    rmdir "$dir" 2>/dev/null || true
  done
  rm -rf "$PREFIX" || true
  update-desktop-database 2>/dev/null || true

  say
  say "Gone: the clone, the filter, the guard, the launcher and the start page."
  say
  say "The machine's own Firefox was never touched — its directory, its profile and"
  say "its policies are all exactly as they were, so it simply carries on.  Nothing"
  say "outside this project's own paths was changed, then or now." 
  say
  say "Two things are deliberately left behind:"
  say "  ~/.local/share/scripture-browser/profile   (the browser's own bookmarks and history)"
  say "  whichever browser this account was told to open links in — set it back with:"
  say "      xdg-settings set default-web-browser firefox.desktop"
  exit 0
fi

# =================================================================== sync ===
# A clone never updates itself.  This is how a security update reaches it: the
# package manager patches the machine's Firefox, and this notices and re-vendors.
if [ "$mode" = sync ]; then
  new_id=$(build_id "$FIREFOX_DIR" || true)
  old_id=$(build_id "$PREFIX/lib/firefox" || true)
  if [ -z "$new_id" ]; then
    say "no Firefox on this machine to re-vendor from"; exit 0
  fi
  if [ -n "$old_id" ] && [ "$old_id" = "$new_id" ]; then
    exit 0                       # nothing has moved; say nothing, do nothing
  fi
  say "The machine's Firefox is now $new_id; the clone is ${old_id:-not there yet}."
  say "Re-vendoring…"
fi

# ================================================================== build ===
case "${home_server:-}" in
  http) HOME_URL="http://127.0.0.1:8787/" ;;
  file) HOME_URL="file://$PREFIX/home/index.html" ;;
  *)    HOME_URL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["home"].get("url") or "")' \
                   "$HERE/config.json") ;;
esac
[ -n "$HOME_URL" ] || HOME_URL="file://$PREFIX/home/index.html"

if [ "$do_build" = 1 ]; then
  say "Building from allowlist.txt…"
  if [ "$HOME_URL" = "file://$PREFIX/home/index.html" ]; then
    "$HERE/build.py"
  else
    "$HERE/build.py" --home-url "$HOME_URL"
  fi
fi
[ -d "$TREE" ] || die "$TREE is missing — run: $HERE/build.py"
[ -f "$MANIFEST" ] || die "$MANIFEST is missing — run: $HERE/build.py"

# ============================================================ open, vendor ===
if [ -x "$GUARD" ] && [ -f "$GUARD_DIR/MANIFEST" ]; then
  "$GUARD" unfreeze || true
fi

if [ -f "$HERE/vendor.sh" ]; then
  if [ "$mode" = sync ]; then
    "$HERE/vendor.sh" --force || warn "vendor.sh did not finish"
  else
    "$HERE/vendor.sh" || warn "vendor.sh did not finish (is Firefox installed?)"
  fi
else
  warn "no vendor.sh beside install.sh: the clone has not been updated"
fi

# ============================================================= copy =========
say "Installing…"
installed=0
while IFS=$'\t' read -r d_path d_mode d_freeze d_sha d_sealed; do
  case "$d_path" in ''|'#'*) continue ;; esac
  install -D -m "$d_mode" "$TREE/$d_sealed" "/$d_path"
  installed=$((installed + 1))
done < "$MANIFEST"
systemctl daemon-reload 2>/dev/null || true
say "  $installed files placed"

# Which start page this install got, in the one place every later run reads it.
install -d -m 755 "$PREFIX"
printf '%s\n' "$HOME_URL" > "$PREFIX/HOME"

# ================================================================ guard =====
filter_started=no
if [ "$do_guard" = 1 ]; then
  "$GUARD" seal "$TREE" "$MANIFEST"

  for unit in "${UNITS[@]}"; do
    [ -f "/usr/lib/systemd/system/scripture-browser-$unit" ] || continue
    case "$unit" in
      home.service)
        if [ "${home_server:-file}" = http ]; then
          systemctl enable --now "scripture-browser-$unit" 2>/dev/null \
            || warn "could not start scripture-browser-$unit"
        else
          systemctl disable --now "scripture-browser-$unit" 2>/dev/null || true
        fi
        ;;
      filter.service)
        if systemctl enable --now "scripture-browser-$unit" 2>/dev/null; then
          filter_started=yes
        else
          warn "could not start the filter"
        fi
        ;;
      *)
        systemctl enable --now "scripture-browser-$unit" 2>/dev/null \
          || warn "could not enable scripture-browser-$unit"
        ;;
    esac
  done
  systemctl reset-failed 2>/dev/null || true

  "$GUARD" restore >/dev/null 2>&1 \
    || warn "the guard could not put everything back — check: scripture-browser --status"

  if command -v systemd-analyze >/dev/null 2>&1; then
    for unit in "${UNITS[@]}"; do
      [ -f "/usr/lib/systemd/system/scripture-browser-$unit" ] || continue
      systemd-analyze verify "/usr/lib/systemd/system/scripture-browser-$unit" >/dev/null 2>&1 \
        || warn "systemd is unhappy about scripture-browser-$unit"
    done
  fi
else
  warn "installed without the guard: the locks are on, but nothing will put them"
  warn "back if they are deleted"
fi

# =============================================================== report =====
sites=$(grep -cvE '^[[:space:]]*(#|$)' "$HERE/allowlist.txt" || true)
clone_id=$(build_id "$PREFIX/lib/firefox" || true)
say
say "Scripture Browser is installed."
say "  sites on the list    $sites"
say "  browser              $PREFIX/lib/firefox  (Firefox ${clone_id:-?}, cloned from $FIREFOX_DIR)"
say "  start page           $HOME_URL"
say "  filter               $filter_started  — the browser reaches nothing without it"
say "  the list to edit     $PREFIX/allowlist.txt"
say "  guard status         scripture-browser --status"
say
if [ "$filter_started" != yes ]; then
  warn "The filter is not running.  Until it is, the browser is pointed at a proxy"
  warn "that is not there, so it will reach nothing at all.  Start it with:"
  warn "  sudo systemctl start scripture-browser-filter.service"
fi
if [ -n "$run_as_user" ]; then
  if runuser -u "$run_as_user" -- "$LAUNCHER" --install-user >/dev/null 2>&1; then
    say "  $run_as_user now opens links in it"
  else
    warn "could not set $run_as_user's default browser from here; as them, run:"
    warn "  scripture-browser --install-user"
  fi
else
  say "To make it this account's default browser, run as yourself:"
  say "  scripture-browser --install-user"
fi
say
say "Firefox updates reach the clone like this: the package manager patches the"
say "machine's Firefox, and within half an hour the clone is re-vendored from it."
say "Nothing else is needed.  When you want it gone:"
say "  sudo scripture-browser --release"
