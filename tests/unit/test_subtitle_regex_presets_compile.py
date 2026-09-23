"""Every regex preset compiles alone and stacked in every order (S10 gate).

Presets are appended to the user's pattern with ``|``. A global inline flag such
as ``(?m)`` anywhere but position 0 is a hard ``re.error`` on Python >= 3.11,
which the parser swallows into "filter disabled" — so a preset may carry none.
"""

from __future__ import annotations

import itertools

from anki_miner.gui.widgets.panels.sentences_settings_panel import SUBTITLE_REGEX_PRESETS
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

PATTERNS = [pattern for _label, pattern in SUBTITLE_REGEX_PRESETS]


def test_no_preset_carries_an_inline_flag():
    assert not [pattern for pattern in PATTERNS if "(?" in pattern.replace("(?:", "").replace("(?<", "")]


def test_each_preset_compiles_alone():
    for pattern in PATTERNS:
        compile_subtitle_regex_filter(pattern, "")


def test_presets_compile_stacked_in_every_order():
    for order in itertools.permutations(PATTERNS):
        compile_subtitle_regex_filter("|".join(order), "")
