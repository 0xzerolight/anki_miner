"""PyInstaller hook for jieba (Chinese word segmentation).

jieba's model lives in data files inside the package — ``dict.txt`` (the main
lexicon) plus the pickled HMM tables under ``finalseg/`` and ``posseg/``
(``prob_*.p``, ``char_state_tab.p``). None of them is reachable by bytecode
analysis, so the frozen app would import jieba and then die building its
tokenizer. ``jieba.posseg`` is imported function-locally in
anki_miner/languages/zh/tokenizer.py; it is listed here as well as in the spec
so a refactor of that import cannot drop the POS tagger from the graph.

Collects nothing when jieba is absent: PyInstaller runs a hook only for a
package that is already in the import graph.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("jieba")
hiddenimports = collect_submodules("jieba") + ["jieba.posseg", "jieba.finalseg"]
