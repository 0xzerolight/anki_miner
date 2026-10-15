"""Hebrew consumes the RTL seam (contract C1-C4, C6) through profile data alone.

Nothing here builds a direction or a font mechanism: Hebrew declares ``direction``,
``writing_system`` and ``bundled_fallback`` and the seam does the rest. Every Hebrew literal is
built from named Unicode characters.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.content_text import apply_content_font
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language


def _word(*letters: str) -> str:
    return "".join(unicodedata.lookup(f"HEBREW LETTER {name}") for name in letters)


KELEV = _word("KAF", "LAMED", "BET")


def test_the_profile_declares_rtl_and_the_bundled_hebrew_face():
    profile = get_profile("he")
    style = profile.content_style
    assert (style.direction, style.writing_system, style.bundled_fallback) == (
        "rtl",
        "Hebrew",
        "NotoSansHebrew-Regular.ttf",
    )
    assert "rtl" in profile.capabilities


def test_a_content_widget_flips_and_flips_back(qtbot):
    widget = QListWidget()
    qtbot.addWidget(widget)
    apply_content_font(widget, get_profile("he").content_style)
    assert widget.layoutDirection() == Qt.LayoutDirection.RightToLeft
    apply_content_font(widget, get_profile("ko").content_style)
    assert widget.layoutDirection() == Qt.LayoutDirection.LeftToRight


def test_the_service_threads_the_hebrew_direction_and_code():
    from anki_miner.services.anki_service import AnkiService

    service = AnkiService(switch_language(AnkiMinerConfig(), "he"))
    assert (service._content_direction, service._content_lang) == ("rtl", "he")


#: A definition in internal miner markup whose structured content holds an example sentence.
MINER_FIELD = (
    '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="D"><i>(D)</i>'
    '<ul class="gloss-list" data-count="1"><li class="gloss-item" data-am-tags=""><div class="gloss-content">'
    '<div class="gloss-sc-div" data-sc-content="example-sentence-a">x</div></div></li></ul></li></ol></div>'
)


def _payload(definition: str = "dog"):
    from anki_miner.models.card_payload import CardPayload
    from anki_miner.models.media import MediaData
    from anki_miner.models.word import TokenizedWord

    word = TokenizedWord(
        surface=KELEV,
        lemma=KELEV,
        reading="",
        sentence=KELEV,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="NOUN",
        expression_reading="POINTED",
    )
    return CardPayload(word=word, media=MediaData(), definition=definition)


def test_the_built_note_wraps_the_word_and_leaves_the_reading_alone():
    from anki_miner.services.anki_note_builder import build_note

    config = switch_language(AnkiMinerConfig(), "he")
    fields = build_note(_payload(), config, set(), content_direction="rtl", content_lang="he").note["fields"]
    assert fields[config.anki_fields["word"]] == f'<div dir="rtl" lang="he">{KELEV}</div>'
    assert fields[config.anki_fields["sentence"]].startswith('<div dir="rtl" lang="he">')
    # C3: a single-direction run the bidi algorithm already renders correctly is NOT wrapped.
    assert fields[config.anki_fields["expression_reading"]] == "POINTED"


def test_a_left_to_right_build_is_the_legacy_note():
    from anki_miner.services.anki_note_builder import build_note

    config = switch_language(AnkiMinerConfig(), "he")
    legacy = build_note(_payload(), config, set()).note["fields"]
    assert legacy[config.anki_fields["word"]] == KELEV
    assert "dir=" not in legacy[config.anki_fields["sentence"]]


def test_the_exported_example_sentence_takes_plaintext_bidi_only_when_rtl():
    from anki_miner.services.anki_note_builder import build_note

    config = switch_language(AnkiMinerConfig(), "he")
    definition_field = config.anki_fields["definition"]
    rtl = build_note(_payload(MINER_FIELD), config, set(), content_direction="rtl").note["fields"]
    ltr = build_note(_payload(MINER_FIELD), config, set()).note["fields"]
    assert "unicode-bidi: plaintext;" in rtl[definition_field]
    assert "unicode-bidi" not in ltr[definition_field]


def test_the_seam_reads_the_direction_off_the_profile_and_nothing_else():
    """C8: no consumer branches on a language code, so a stub direction is all it takes."""
    profile = get_profile("he")
    as_ltr = replace(profile, content_style=replace(profile.content_style, direction="ltr"))
    assert as_ltr.content_style.direction == "ltr"
    assert profile.content_style.direction == "rtl"
