"""PyInstaller hook for gTTS.

gTTS imports its tokenizer/lang submodules in ways PyInstaller's static
analysis can miss. collect_submodules ensures they ship. No data files
needed (gTTS ships only a hardcoded language table, no resources).
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [m for m in collect_submodules("gtts") if ".tests" not in m]
