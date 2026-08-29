"""PyInstaller hook for kiwipiepy (Korean morphological analyser).

kiwipiepy is a package with a compiled extension and small package data (tag
tables, typo dictionaries) inside the package directory, so collect_all covers
both. Harmless when kiwipiepy is absent: find_spec returns None on builds
without the [ko] extra and every list stays empty (hook-pywhispercpp.py's
find_spec-gated pattern, PyInstaller-Hooks/hook-pywhispercpp.py:79-83).
"""

import importlib.util

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

try:
    _spec = importlib.util.find_spec("kiwipiepy")
except Exception:  # noqa: BLE001 - a broken/absent install means "nothing to collect"
    _spec = None

if _spec is not None:
    from PyInstaller.utils.hooks import collect_all

    datas, binaries, hiddenimports = collect_all("kiwipiepy")
