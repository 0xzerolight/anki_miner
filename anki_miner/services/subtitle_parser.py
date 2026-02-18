"""Service for parsing subtitles and extracting vocabulary."""

from pathlib import Path

import fugashi
import pysubs2

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import TokenizedWord
from anki_miner.utils import clean_subtitle_text, generate_furigana


class SubtitleParserService:
    """Parse subtitles and extract Japanese vocabulary words (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the subtitle parser.

        Args:
            config: Configuration for parsing
        """
        self.config = config
        self.tagger = fugashi.Tagger()

    def parse_raw_entries(self, subtitle_file: Path) -> list[tuple[float, float, str]]:
        """Parse subtitle file and return raw timing entries without tokenization.

        Args:
            subtitle_file: Path to subtitle file (.ass, .srt, .ssa)

        Returns:
            List of (start_seconds, end_seconds, text) tuples

        Raises:
            SubtitleParseError: If subtitle file cannot be parsed
        """
        try:
            subs = pysubs2.load(str(subtitle_file))
        except FileNotFoundError as e:
            raise SubtitleParseError(f"Subtitle file not found: {subtitle_file}") from e
        except Exception as e:
            raise SubtitleParseError(f"Failed to parse subtitle file: {e}") from e

        entries = []
        for line in subs:
            text = clean_subtitle_text(line.text)
            if not text:
                continue

            start_time = max(0.0, (line.start / 1000.0) + self.config.subtitle_offset)
            end_time = max(start_time, (line.end / 1000.0) + self.config.subtitle_offset)
            entries.append((start_time, end_time, text))

        return entries

    def parse_subtitle_file(self, subtitle_file: Path) -> list[TokenizedWord]:
        """Parse subtitle file and extract vocabulary words.

        Args:
            subtitle_file: Path to subtitle file (.ass, .srt, .ssa)

        Returns:
            List of TokenizedWord objects

        Raises:
            SubtitleParseError: If subtitle file cannot be parsed
        """
        try:
            subs = pysubs2.load(str(subtitle_file))
        except FileNotFoundError as e:
            raise SubtitleParseError(f"Subtitle file not found: {subtitle_file}") from e
        except Exception as e:
            raise SubtitleParseError(f"Failed to parse subtitle file: {e}") from e

        all_words = []
        seen_words: set[str] = set()  # Track unique words by lemma AND surface

        for line in subs:
            text = clean_subtitle_text(line.text)

            if not text:
                continue

            # Convert timing from milliseconds to seconds and apply offset
            start_time = max(0.0, (line.start / 1000.0) + self.config.subtitle_offset)
            end_time = max(start_time, (line.end / 1000.0) + self.config.subtitle_offset)
            duration = end_time - start_time

            # Tokenize with MeCab
            for word_token in self.tagger(text):
                if not self._should_include_word(word_token):
                    continue

                # Get lemma (dictionary form) for lookups and deduplication
                lemma = self._extract_lemma(word_token)
                surface = word_token.surface

                # Skip if we've already seen this word
                if lemma in seen_words or surface in seen_words:
                    continue
                seen_words.add(lemma)
                seen_words.add(surface)

                # Get reading if available
                reading = self._extract_reading(word_token)

                # Generate furigana annotations
                expression_furigana = generate_furigana(lemma, self.tagger)
                sentence_furigana = generate_furigana(text, self.tagger)

                all_words.append(
                    TokenizedWord(
                        surface=surface,
                        lemma=lemma,
                        reading=reading,
                        sentence=text,
                        start_time=start_time,
                        end_time=end_time,
                        duration=duration,
                        expression_furigana=expression_furigana,
                        sentence_furigana=sentence_furigana,
                    )
                )

        return all_words

    def _extract_lemma(self, word_token) -> str:
        """Extract lemma (dictionary form) from word token.

        Args:
            word_token: MeCab word token

        Returns:
            Lemma string
        """
        try:
            lemma = word_token.feature.lemma or word_token.surface
        except AttributeError:
            lemma = word_token.surface

        # Clean lemma - remove English translations or POS tags after hyphens
        # e.g., "スクランブル-scramble" -> "スクランブル"
        if "-" in lemma:
            lemma = lemma.split("-")[0]

        return str(lemma)

    def _extract_reading(self, word_token) -> str:
        """Extract kana reading from word token.

        Args:
            word_token: MeCab word token

        Returns:
            Kana reading string
        """
        try:
            return str(word_token.feature.kana or word_token.surface)
        except AttributeError:
            return str(word_token.surface)

    def _should_include_word(self, word_token) -> bool:
        """Determine if a word should be included based on POS and other criteria.

        Args:
            word_token: MeCab word token

        Returns:
            True if word should be included, False otherwise
        """
        surface = word_token.surface

        # Skip empty or whitespace-only tokens
        if not surface or not surface.strip():
            return False

        # Skip words that are too short
        if len(surface) < self.config.min_word_length:
            return False

        # Get part-of-speech tags
        try:
            pos1 = word_token.feature.pos1  # Main POS
            pos2 = word_token.feature.pos2  # Sub POS
        except AttributeError:
            return False

        # Skip particles, auxiliary verbs, symbols, punctuation
        if pos1 in ["助詞", "助動詞", "記号", "補助記号"]:
            return False

        # Skip interjections and fillers
        if pos1 in ["感動詞", "フィラー"]:
            return False

        # Check if it's a content word (noun, verb, adjective, adverb)
        if pos1 not in self.config.allowed_pos:
            return False

        # Check for excluded subtypes
        if pos2 and pos2 in self.config.excluded_subtypes:
            return False

        # Skip if no lemma available
        try:
            lemma = word_token.feature.lemma
            if not lemma:
                return False
        except AttributeError:
            return False

        # Check if word contains meaningful characters
        has_kanji = any("\u4e00" <= c <= "\u9fff" for c in surface)
        is_katakana = all("\u30a0" <= c <= "\u30ff" or c in "ー・" for c in surface if c.strip())

        # For katakana-only words, apply stricter filtering
        if is_katakana and not has_kanji:
            # Skip onomatopoeia patterns
            stripped = surface.replace("ッ", "").replace("ー", "").replace("・", "")
            unique_chars = set(stripped)

            # If only 1-2 unique characters, likely onomatopoeia
            if len(unique_chars) <= 2 and len(surface) <= 4:
                return False

            # If ends in small tsu and is short, likely sound effect
            if surface.endswith("ッ") and len(surface) <= 3:
                return False

            # Must be at least 2 chars to be valid katakana word
            return len(surface) >= 2

        # For words with kanji
        if has_kanji:
            # Single kanji alone is often a fragment
            kanji_count = sum(1 for c in surface if "\u4e00" <= c <= "\u9fff")
            return not (kanji_count == 1 and len(surface) == 1)

        return False
