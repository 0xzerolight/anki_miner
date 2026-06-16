"""Deterministic, copyright-free Japanese subtitle for the E2E harness.

Four short ORIGINAL sentences (not lifted from any work), all timed inside the
[0, 10] s window of :mod:`tests.e2e.fixtures_media`'s clip so every line falls
within the video. The lines were chosen to yield a fixed set of mineable
content words (verbs / adjectives / nouns) with no surprises from the
tokenizer.

``EXPECTED_LEMMAS`` is the authoritative set of lemmas the subtitle yields. It
was derived by running the REAL tokenizer
(:class:`anki_miner.services.subtitle_parser.SubtitleParserService`) over the
exact SRT this module writes, with a default :class:`AnkiMinerConfig`, and
collecting ``word.lemma`` for every mined word. The
``test_fixtures.py::test_subtitle_yields_expected_lemmas`` test re-runs that
tokenizer and asserts the result still equals this constant, so the value can
never silently drift from reality. ``LEMMA_READINGS`` (lemma → kana reading,
also captured from the tokenizer) is shared with
:mod:`tests.e2e.fixtures_dictionary` so the seeded offline dictionary stays
consistent with what the pipeline will actually look up.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "ASSETS_DIR",
    "TEST_SRT_PATH",
    "SUBTITLE_LINES",
    "EXPECTED_LEMMAS",
    "LEMMA_READINGS",
    "write_test_srt",
    "get_test_srt",
]

#: Directory holding the committed E2E input assets (shared with fixtures_media).
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
#: The committed subtitle asset.
TEST_SRT_PATH = ASSETS_DIR / "e2e.srt"

#: ``(start_sec, end_sec, text)`` lines. All within [0, 10] s (the clip length).
#: Original sentences; not copied from any copyrighted source.
SUBTITLE_LINES: tuple[tuple[float, float, str], ...] = (
    (0.5, 2.5, "新しい本を買いました"),
    (3.0, 5.0, "今日は学校で勉強する"),
    (5.5, 7.5, "美味しい料理を食べる"),
    (8.0, 9.5, "友達と公園を走る"),
)

#: Lemma -> kana reading, captured from the real tokenizer (the lemma's OWN
#: reading, e.g. 買う -> かう, not the surface 買い -> かい). Shared with the
#: dictionary fixture so seeded readings match pipeline lookups. Insertion order
#: follows first appearance across ``SUBTITLE_LINES``.
LEMMA_READINGS: dict[str, str] = {
    "新しい": "あたらしい",
    "本": "ほん",
    "買う": "かう",
    "今日": "きょう",
    "学校": "がっこう",
    "勉強": "べんきょう",
    "美味しい": "おいしい",
    "料理": "りょうり",
    "食べる": "たべる",
    "友達": "ともだち",
    "公園": "こうえん",
    "走る": "はしる",
}

#: The mineable lemmas the subtitle yields, in tokenizer order. Derived by
#: running the real SubtitleParserService over the SRT (see module docstring);
#: ``test_fixtures.py`` re-derives and asserts equality so this never drifts.
EXPECTED_LEMMAS: tuple[str, ...] = tuple(LEMMA_READINGS)


def _format_timestamp(seconds: float) -> str:
    """Render *seconds* as an SRT ``HH:MM:SS,mmm`` timestamp."""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_test_srt(path: Path) -> Path:
    """Write the canonical E2E subtitle (SRT) to *path*.

    Block shape mirrors the integration suite's ``_write_srt`` helper: an index,
    a ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` timing line, the text, then a blank line
    separating blocks.

    Args:
        path: Destination ``.srt`` path (parent dirs created as needed).

    Returns:
        ``path`` (for chaining).
    """
    blocks = [
        f"{i}\n{_format_timestamp(start)} --> {_format_timestamp(end)}\n{text}\n"
        for i, (start, end, text) in enumerate(SUBTITLE_LINES, start=1)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def get_test_srt() -> Path:
    """Return the committed subtitle path, writing it if missing.

    Returns:
        Path to ``tests/e2e/assets/e2e.srt``.
    """
    if not TEST_SRT_PATH.exists():
        write_test_srt(TEST_SRT_PATH)
    return TEST_SRT_PATH
