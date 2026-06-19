"""i18n smoke-tests for EpisodeProcessor presenter strings.

Verifies that the QCoreApplication.translate() wrappers in episode_processor.py
render correctly under the default English translator (no .qm loaded), and that
the _arg() placeholder helper substitutes correctly.
"""

from PyQt6.QtCore import QCoreApplication

from anki_miner.orchestration.episode_processor import _arg


def test_unique_words_plural_renders(qapp):
    """%n in Found %n unique word(s) is replaced by the count at runtime."""
    msg = QCoreApplication.translate("EpisodeProcessor", "Found %n unique word(s)", "", 3)
    assert msg == "Found 3 unique word(s)"


def test_step1_subtitle_arg_renders(qapp):
    """%1 in Step 1/5 message is substituted via _arg()."""
    msg = _arg(
        QCoreApplication.translate("EpisodeProcessor", "Step 1/5 — Parsing subtitles: %1"),
        "episode01.ass",
    )
    assert msg == "Step 1/5 — Parsing subtitles: episode01.ass"


def test_known_word_db_synced_multi_arg_renders(qapp):
    """%1 and %2 in the known-word-DB sync message are both replaced."""
    msg = _arg(
        QCoreApplication.translate("EpisodeProcessor", "Known word DB synced: %1 new words (%2 total)"),
        5,
        200,
    )
    assert msg == "Known word DB synced: 5 new words (200 total)"


def test_comprehension_float_arg_renders(qapp):
    """Formatted float is passed as string arg for the comprehension message."""
    msg = _arg(
        QCoreApplication.translate("EpisodeProcessor", "Comprehension: %1% of words already known"),
        f"{87.3:.1f}",
    )
    assert msg == "Comprehension: 87.3% of words already known"


def test_successfully_created_plural_renders(qapp):
    """%n in 'Successfully created %n card(s)' renders with count."""
    msg = QCoreApplication.translate("EpisodeProcessor", "Successfully created %n card(s)", "", 42)
    assert msg == "Successfully created 42 card(s)"


def test_error_arg_renders(qapp):
    """Error: %1 renders with the exception string."""
    msg = _arg(QCoreApplication.translate("EpisodeProcessor", "Error: %1"), "AnkiConnect timeout")
    assert msg == "Error: AnkiConnect timeout"


def test_i_plus_one_three_arg_renders(qapp):
    """Three-placeholder i+1 filter message renders all substitutions."""
    msg = _arg(
        QCoreApplication.translate("EpisodeProcessor", "i+1 filter: kept %1/%2 words (%3%)"),
        8,
        15,
        "53",
    )
    assert msg == "i+1 filter: kept 8/15 words (53%)"
