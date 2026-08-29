"""PyInstaller hook for pypinyin (word-level Hanzi -> pinyin).

The single-character and phrase tables ship as importable Python modules, which
static analysis does find, but the ``pypinyin.contrib`` / ``pypinyin.style``
plug-in modules are resolved by name at runtime and the package also carries
non-Python data. Sweeping both keeps the phrase dictionary in the bundle —
without it every reading degrades to per-character pinyin and polyphones
(重要 vs 重复) come out wrong instead of missing, which no smoke would catch.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("pypinyin")
hiddenimports = collect_submodules("pypinyin")
