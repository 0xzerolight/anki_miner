"""PyInstaller hook for yt-dlp.

yt-dlp lazy-loads ~1600 extractor modules at runtime plus optional deps
(websockets, mutagen, brotli) that PyInstaller's static analysis misses.
collect_all sweeps everything in one call.

This adds ~50-100MB per OS binary. Do NOT try to trim the extractor bundle
with --exclude-module: yt-dlp's extractor registry has internal
cross-dependencies and breaks when modules are pruned piecemeal.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("yt_dlp")
