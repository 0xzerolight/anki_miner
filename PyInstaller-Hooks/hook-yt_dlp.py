"""PyInstaller hook for yt-dlp.

yt-dlp lazy-loads ~1600 extractor modules at runtime plus optional deps
(websockets, mutagen, brotli) that PyInstaller's static analysis misses.
collect_all sweeps everything in one call.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("yt_dlp")
