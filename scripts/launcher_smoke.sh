#!/usr/bin/env bash
# Smoke test for packaging/linux-launcher.sh — the AppImage's AppRun and the
# .deb's /usr/bin entrypoint.
#
# WHY THIS EXISTS. The shim became the Linux entrypoint, and nothing else ever
# runs it: scripts/bundle_smoke.sh execs dist/AnkiMiner directly, and shellcheck
# only reads it. A bug in the shim does not degrade the fix — it bricks the
# artifact, because AppRun is what the AppImage runtime execs.
#
# It drives the REAL script against a throwaway AppDir, so the path exercised is
# the one the AppImage takes: an AppRun symlink resolved through `readlink -f`,
# not an ANKI_MINER_BUNDLE_DIR shortcut.
#
# The bundled libstdc++ is a fixture, not a real library: _max_glibcxx greps
# version strings, so a text file carrying "GLIBCXX_3.4.20" is an old runtime and
# one carrying "GLIBCXX_3.4.99" is a newer-than-host one. That makes BOTH
# directions of the version compare testable on any host, which a real bundle
# cannot do.
#
# Usage: scripts/launcher_smoke.sh [dist_dir]
#   dist_dir (optional, e.g. dist/AnkiMiner) additionally asserts that the real
#   bundle still ships the libstdc++ the shim exists to shadow.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="$REPO_ROOT/packaging/linux-launcher.sh"
DIST="${1:-}"

failures=0

pass() { echo "  ok   - $1"; }
fail() {
    echo "::error::launcher smoke: $1"
    echo "  FAIL - $1"
    failures=$((failures + 1))
}

check_contains() {
    # check_contains <label> <haystack> <needle>
    case "$2" in
    *"$3"*) pass "$1" ;;
    *) fail "$1 (expected '$3' in: $2)" ;;
    esac
}

check_equals() {
    # check_equals <label> <actual> <expected>
    if [ "$2" = "$3" ]; then
        pass "$1"
    else
        fail "$1 (expected '$3', got '$2')"
    fi
}

# Build an AppDir mirroring packaging/appimage/build-appimage.sh: the launcher at
# usr/bin/anki-miner-launcher, the app beside it, and AppRun as a relative
# symlink to the launcher.
make_appdir() {
    # make_appdir <dir> <bundled-glibcxx-version|"none">
    local dir="$1" version="$2"
    mkdir -p "$dir/usr/bin/_internal"
    install -m 0755 "$LAUNCHER" "$dir/usr/bin/anki-miner-launcher"
    cat >"$dir/usr/bin/AnkiMiner" <<'STUB'
#!/usr/bin/env bash
echo "LD_PRELOAD=${LD_PRELOAD:-}"
echo "ARGS=$*"
STUB
    chmod +x "$dir/usr/bin/AnkiMiner"
    if [ "$version" != "none" ]; then
        printf 'GLIBCXX_%s\n' "$version" >"$dir/usr/bin/_internal/libstdc++.so.6"
    fi
    ln -sf usr/bin/anki-miner-launcher "$dir/AppRun"
}

field() {
    # field <output> <NAME> -> the value of the stub's NAME= line
    printf '%s\n' "$1" | sed -n "s/^$2=//p"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== launcher smoke =="

# 1. Host runtime newer than the bundle: preload, and reach the app through the
#    AppRun symlink with arguments intact. This is the AppImage's real path.
make_appdir "$TMP/old" "3.4.20"
out=$("$TMP/old/AppRun" --one "two words" 2>&1)
check_contains "AppRun symlink reaches the app" "$out" "ARGS="
check_equals "arguments pass through" "$(field "$out" ARGS)" "--one two words"
check_contains "host libstdc++ is preloaded" "$(field "$out" LD_PRELOAD)" "libstdc++.so.6"
check_contains "host libgcc_s travels with it" "$(field "$out" LD_PRELOAD)" "libgcc_s.so.1"

# 2. Debian leaves sbin off a non-root PATH. The shim must still find ldconfig —
#    otherwise it silently no-ops for the entire .deb audience.
out=$(env PATH=/usr/bin:/bin "$TMP/old/AppRun" 2>&1)
check_contains "ldconfig resolves without sbin on PATH" "$(field "$out" LD_PRELOAD)" "libstdc++.so.6"

# 3. Bundled runtime newer than the host: the bundled copy is the one that works
#    and must stay in front. libstdc++ is only forward compatible.
make_appdir "$TMP/new" "3.4.99"
out=$("$TMP/new/AppRun" 2>&1)
check_equals "newer bundled runtime is left alone" "$(field "$out" LD_PRELOAD)" ""

# 4. Nothing bundled: nothing to shadow, nothing to do.
make_appdir "$TMP/bare" "none"
out=$("$TMP/bare/AppRun" 2>&1)
check_equals "no bundled runtime means no preload" "$(field "$out" LD_PRELOAD)" ""

# 5. The documented escape hatch.
out=$(ANKI_MINER_NO_CXX_SHIM=1 "$TMP/old/AppRun" 2>&1)
check_equals "ANKI_MINER_NO_CXX_SHIM=1 changes nothing" "$(field "$out" LD_PRELOAD)" ""

# 6. An existing LD_PRELOAD is prepended to, never replaced.
out=$(LD_PRELOAD=/tmp/someone-elses.so "$TMP/old/AppRun" 2>&1)
check_contains "existing LD_PRELOAD is preserved" "$(field "$out" LD_PRELOAD)" "/tmp/someone-elses.so"

# 7. Against a real bundle: the shim only matters while PyInstaller keeps pulling
#    the C++ runtime into _internal. If that ever stops, this says so out loud
#    instead of leaving dead code that looks like a fix.
if [ -n "$DIST" ]; then
    if [ -e "$DIST/_internal/libstdc++.so.6" ]; then
        pass "bundle still ships _internal/libstdc++.so.6"
    else
        fail "bundle has no _internal/libstdc++.so.6 — the shim now shadows nothing"
    fi
fi

if [ "$failures" -ne 0 ]; then
    echo "== launcher smoke FAILED ($failures) =="
    exit 1
fi
echo "== launcher smoke OK =="
