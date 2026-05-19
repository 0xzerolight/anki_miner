"""Data models for vocabulary words."""

from dataclasses import dataclass
from pathlib import Path

from anki_miner.models.media import MediaData


@dataclass
class TokenizedWord:
    """A word extracted from subtitles with timing information."""

    surface: str  # Surface form (as it appears in text)
    lemma: str  # Dictionary form (base form)
    reading: str  # Kana reading
    sentence: str  # Original sentence context
    start_time: float  # Start time in seconds
    end_time: float  # End time in seconds
    duration: float  # Duration in seconds
    video_file: Path | None = None  # Source video (for batch processing)
    expression_furigana: str = ""  # Furigana for expression, e.g. "食べる[たべる]"
    expression_reading: str = ""  # Plain kana reading of expression, e.g. "たべる"
    sentence_furigana: str = ""  # Furigana for sentence, e.g. "日本語[にほんご]を食べる[たべる]。"
    sentence_reading: str = ""  # Plain kana reading of sentence, e.g. "にほんごをたべる。"
    frequency_rank: int | None = None  # Word frequency rank (1 = most common)
    pos: str | None = None  # MeCab pos1 (動詞/形容詞/名詞/...) — used for kifuku/odaka distinction

    @property
    def mined_form(self) -> str:
        """The form that becomes the card front (Expression field).

        Verbs and adjectives mine as lemma (dictionary form) so that
        ``破れ`` becomes ``破れる`` — the learner studies the form that
        recognizes/produces every conjugation (Issue #19).

        Nouns and other non-conjugating POS keep the surface form: unidic
        sometimes maps homograph-like nouns to a different headword
        (``豪腕`` → ``剛腕``); preserving surface for nouns avoids that
        regression (Issue #5).
        """
        return self.lemma if self.pos in ("動詞", "形容詞") else self.surface

    def __str__(self) -> str:
        return f"{self.lemma} ({self.reading})"

    def __repr__(self) -> str:
        return f"TokenizedWord(lemma='{self.lemma}', reading='{self.reading}', surface='{self.surface}')"


@dataclass(frozen=True)
class LineLemmas:
    """All content-word lemmas on a single subtitle line.

    Used by the i+1 sentence filter to count unknown lemmas per line
    without re-tokenizing. Frozen so instances can be hashed and shared
    safely across the worker thread boundary.
    """

    line_text: str  # Cleaned (post-regex-filter) subtitle text
    lemmas: frozenset[str]  # Content-word lemmas after compound-merge + _should_include_word
    start_time: float  # Start time in seconds (post-offset)
    end_time: float  # End time in seconds (post-offset)
    duration: float  # end_time - start_time
    sentence_furigana: str = ""  # Furigana annotation for the whole line
    sentence_reading: str = ""  # Plain-kana reading for the whole line


@dataclass
class WordData:
    """Complete data for a vocabulary word including definition and media."""

    word: TokenizedWord
    definition: str | None = None
    screenshot_path: Path | None = None
    audio_path: Path | None = None
    media: MediaData | None = None
    pitch_position: str | None = None
    pitch_category: str | None = None
    frequency_rank: int | None = None

    @property
    def has_media(self) -> bool:
        """Check if word has any media (screenshot or audio)."""
        return self.screenshot_path is not None or self.audio_path is not None

    @property
    def has_definition(self) -> bool:
        """Check if word has a definition."""
        return self.definition is not None and len(self.definition) > 0

    def __str__(self) -> str:
        return f"{self.word.lemma}: {self.definition[:50] if self.definition else 'No definition'}"
