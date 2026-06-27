"""PyInstaller hook for faster-whisper and ctranslate2.

faster-whisper delegates all compute to ctranslate2, which ships platform-
specific native extension modules and OpenMP runtime libraries that
PyInstaller's static analysis cannot discover.  collect_all on both packages
sweeps datas / binaries / hiddenimports in one pass, including:

  - ctranslate2/*.so / *.pyd (C extensions per OS)
  - OpenMP runtime: libgomp-*.so.1 (Linux), libomp*.dylib (macOS),
    vcomp*.dll (Windows)  — bundled inside the ctranslate2 wheel
  - faster_whisper tokenizer resources (vocabulary/merges files)
  - huggingface_hub / tokenizers / sentencepiece transitive imports

faster_whisper also imports ``av`` (PyAV) unconditionally at package load
(faster_whisper/audio.py: ``import av``), so av must be collected too — it is a
compiled package shipping its own libav* shared libs that static analysis won't
follow.  collect_all here keeps the ASR collection self-contained rather than
relying on pyinstaller-hooks-contrib's hook-av.py being present in the pinned
PyInstaller.  (av is stripped from the lean .deb stage tree separately; see
release.yml.)

Do NOT trim these with --exclude-module: ctranslate2's extension loader
has internal cross-dependencies and breaks when native modules are pruned.
The hook itself adds ~80-120 MB per platform.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("faster_whisper")
_ct2_datas, _ct2_binaries, _ct2_hiddenimports = collect_all("ctranslate2")
_av_datas, _av_binaries, _av_hiddenimports = collect_all("av")

datas += _ct2_datas + _av_datas
binaries += _ct2_binaries + _av_binaries
hiddenimports += _ct2_hiddenimports + _av_hiddenimports
