"""PyInstaller hook for OpenCC (simplified/traditional conversion).

The wheel ships a compiled ``opencc/clib`` extension plus its conversion
dictionaries (``share/opencc/*.json`` + ``*.ocd2``) — a binary and a data tree,
neither of which bytecode analysis reaches. ``collect_all`` sweeps datas,
binaries and submodules in one pass, matching hook-faster_whisper's shape.

OpenCC is optional at runtime (languages/zh/variants.py degrades to "no script
variants" without it), so a build whose install target omitted the zh extra
simply never triggers this hook.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("opencc")
