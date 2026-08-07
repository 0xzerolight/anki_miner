#!/usr/bin/env bash
# Launcher shim for the Linux PyInstaller bundle (AppImage and .deb both use it).
#
# THE PROBLEM. PyInstaller's bootloader puts the bundle's _internal directory on
# LD_LIBRARY_PATH, and Qt pulls libstdc++.so.6 / libgcc_s.so.1 into that
# directory (PyInstaller's own exclude list covers GL/EGL/drm/xcb/wayland/nvidia
# but not the C++ runtime). When Qt brings up an OpenGL context, the HOST's Mesa
# DRI driver is dlopened into this process — and it resolves libstdc++ from
# LD_LIBRARY_PATH, i.e. ours. A host driver built against a newer libstdc++ than
# the bundled one then fails to resolve its symbols and aborts the process
# outright: no Python traceback, nothing catchable. That is how "every video
# mining run crashes" was reported from Arch, where Mesa tracks far ahead of the
# ubuntu-22.04 runtime the release artifacts are built with.
#
# THE FIX. If the host's libstdc++ is at least as new as ours, LD_PRELOAD it.
# LD_PRELOAD is consulted before LD_LIBRARY_PATH, so both the app and any
# dlopened driver get one consistent, sufficiently-new C++ runtime.
#
# Why not simply drop the C++ runtime from the bundle: libstdc++ is only
# FORWARD compatible. Dropping it fixes hosts newer than the build machine and
# breaks every host older than it. Preferring the newer of the two is correct in
# both directions, which is the whole point of doing this at runtime.
#
# FAIL-SAFE BY CONSTRUCTION. Every step below degrades to "change nothing", so
# the worst case is exactly today's behaviour. Never add `set -e` here: a failed
# probe must not stop the app from starting.

set -u

_bundle_dir="${ANKI_MINER_BUNDLE_DIR:-$(dirname "$(readlink -f "$0")")}"
_internal="$_bundle_dir/_internal"

# Highest GLIBCXX_3.4.N version symbol present in a libstdc++, or "" if it
# cannot be read. Plain grep/sort only — no objdump/strings on the target host.
_max_glibcxx() {
    [ -r "$1" ] || return 0
    LC_ALL=C grep -ao 'GLIBCXX_3\.4\.[0-9]\+' "$1" 2>/dev/null | sort -u -V | tail -1
}

_prefer_host_cxx_runtime() {
    local bundled="$_internal/libstdc++.so.6"
    # Nothing bundled means nothing to shadow the host with. Done.
    [ -e "$bundled" ] || return 0

    local host
    host=$(ldconfig -p 2>/dev/null | awk '/libstdc\+\+\.so\.6 \(libc6,x86-64\)/ {print $NF; exit}')
    [ -n "$host" ] && [ -r "$host" ] || return 0

    local host_ver bundled_ver newest
    host_ver=$(_max_glibcxx "$host")
    bundled_ver=$(_max_glibcxx "$bundled")
    [ -n "$host_ver" ] && [ -n "$bundled_ver" ] || return 0

    # Preload only when the host is >= bundled. If the host is OLDER, the
    # bundled copy is the one that works and must stay in front.
    newest=$(printf '%s\n%s\n' "$host_ver" "$bundled_ver" | sort -V | tail -1)
    [ "$newest" = "$host_ver" ] || return 0

    local preload="$host"
    # libgcc_s travels with libstdc++; mixing a host libstdc++ with a bundled
    # libgcc_s is its own ABI hazard, so move them together or not at all.
    local host_gcc
    host_gcc=$(ldconfig -p 2>/dev/null | awk '/libgcc_s\.so\.1 \(libc6,x86-64\)/ {print $NF; exit}')
    if [ -n "$host_gcc" ] && [ -r "$host_gcc" ]; then
        preload="$preload:$host_gcc"
    fi

    export LD_PRELOAD="${preload}${LD_PRELOAD:+:$LD_PRELOAD}"
}

# ANKI_MINER_NO_CXX_SHIM=1 skips the whole thing, so a user who hits an
# unforeseen interaction has a way back to the old behaviour without a downgrade.
if [ "${ANKI_MINER_NO_CXX_SHIM:-}" != "1" ]; then
    _prefer_host_cxx_runtime
fi

exec "$_bundle_dir/AnkiMiner" "$@"
