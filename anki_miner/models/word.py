"""Data models for vocabulary words."""

from dataclasses import dataclass
from pathlib import Path


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
    sentence_furigana: str = ""  # Furigana for sentence, e.g. "日本語[にほんご]を食べる[たべる]。"

    def __str__(self) -> str:
        return f"{self.lemma} ({self.reading})"

    def __repr__(self) -> str:
        return f"TokenizedWord(lemma='{self.lemma}', reading='{self.reading}', surface='{self.surface}')"


@dataclass
class WordData:
    """Complete data for a vocabulary word including definition and media."""

    word: TokenizedWord
    definition: str | None = None
    screenshot_path: Path | None = None
    audio_path: Path | None = None
    screenshot_filename: str | None = None
    audio_filename: str | None = None

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
