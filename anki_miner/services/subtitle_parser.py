"""Service for parsing subtitles and extracting vocabulary."""

import logging
import re
from pathlib import Path
from types import SimpleNamespace

import fugashi
import pysubs2

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import TokenizedWord
from anki_miner.utils import clean_subtitle_text, generate_furigana, generate_reading

logger = logging.getLogger(__name__)

_NOMINAL_SUFFIX_POS2 = {"名詞的", "形状詞的", "副詞的"}

# Whitelist of 接頭辞 surfaces that productively form compounds with
# 名詞/形状詞 roots. Used by _merge_prefix_compounds to avoid false positives
# from rare/unproductive 接頭辞 entries in unidic.
_PREFIX_WHITELIST = frozenset(
    {"無", "不", "非", "反", "超", "未", "新", "旧", "全", "半", "副", "元", "再", "最"}
)

# 接尾辞(名詞的) surfaces that nominalize a preceding 動詞 連用形 stem
# (e.g. 言い+方 → 言い方). Restricted to a small productive set; 者/事/物
# etc. are not included because they tokenize differently and would
# over-merge.
_VERB_NOMINALIZER_SUFFIXES = frozenset({"方", "手", "様"})


class _SyntheticToken:
    """Duck-typed token replacement for merged compounds.

    Mimics fugashi token attribute access (.surface,
    .feature.{pos1,pos2,lemma,kana}).
    """

    __slots__ = ("surface", "feature")

    def __init__(self, surface: str, pos1: str, pos2: str, lemma: str, kana: str):
        self.surface = surface
        self.feature = SimpleNamespace(pos1=pos1, pos2=pos2, lemma=lemma, kana=kana)


class SubtitleParserService:
    """Parse subtitles and extract Japanese vocabulary words (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the subtitle parser.

        Args:
            config: Configuration for parsing
        """
        self.config = config
        self.tagger = fugashi.Tagger()
        self._filter_pattern: re.Pattern[str] | None = None
        if config.use_subtitle_regex_filter and config.subtitle_regex_filter:
            try:
                self._filter_pattern = re.compile(config.subtitle_regex_filter)
            except re.error as e:
                # Bad pattern at the boundary should not crash mining. Disable
                # and surface in the log; GUI validation should catch this on save.
                logger.warning(
                    "Invalid subtitle_regex_filter %r: %s; filter disabled for this run",
                    config.subtitle_regex_filter,
                    e,
                )
                self._filter_pattern = None

    def _apply_text_filter(self, text: str) -> str:
        """Apply the configured regex filter to a subtitle line.

        Runs after ``clean_subtitle_text`` strips tags/HTML so the pattern
        operates on human-readable text. Whitespace is renormalized because
        a stripped span can leave double spaces behind.
        """
        if self._filter_pattern is None:
            return text
        filtered = self._filter_pattern.sub(self.config.subtitle_regex_replacement, text)
        return " ".join(filtered.split())

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
            text = self._apply_text_filter(clean_subtitle_text(line.text))
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
            text = self._apply_text_filter(clean_subtitle_text(line.text))

            if not text:
                continue

            # Convert timing from milliseconds to seconds and apply offset
            start_time = max(0.0, (line.start / 1000.0) + self.config.subtitle_offset)
            end_time = max(start_time, (line.end / 1000.0) + self.config.subtitle_offset)
            duration = end_time - start_time

            # Tokenize with MeCab
            raw_tokens = list(self.tagger(text))
            for word_token in self._merge_compound_suffixes(raw_tokens):
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

                # Generate furigana annotations and plain-kana readings
                expression_furigana = generate_furigana(surface, self.tagger)
                sentence_furigana = generate_furigana(text, self.tagger)
                expression_reading = generate_reading(surface, self.tagger)
                sentence_reading = generate_reading(text, self.tagger)

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
                        expression_reading=expression_reading,
                        sentence_furigana=sentence_furigana,
                        sentence_reading=sentence_reading,
                    )
                )

        return all_words

    def _merge_compound_suffixes(self, tokens: list) -> list:
        """Run all compound-merge passes in dependency order.

        Order matters:
        1. _merge_prefix_compounds  — 接頭辞 + 名詞/形状詞 (e.g. 不+可能 → 不可能).
           Must run first so that downstream 名詞-suffix merge sees the
           synthetic 不可能 (pos1=名詞) as a valid head and chains correctly
           into 不可能性, 不可能的, etc.
        2. _merge_noun_suffixes     — 名詞 + 接尾辞(名詞的/形状詞的/副詞的)
           chains (e.g. 刑務+所 → 刑務所, 入院+中+的 → 入院中的).
        3. _merge_verb_nominalizers — 動詞(連用形) + 接尾辞(名詞的) where the
           suffix is a verb-stem nominalizer (方/手/様). Independent of (1)
           and (2) so order is irrelevant.
        """
        tokens = self._merge_prefix_compounds(tokens)
        tokens = self._merge_noun_suffixes(tokens)
        tokens = self._merge_verb_nominalizers(tokens)
        return tokens

    def _merge_noun_suffixes(self, tokens: list) -> list:
        """Merge 名詞 + 接尾辞(名詞的/形状詞的/副詞的) chains into a single token.

        Walks tokens left-to-right. When a 名詞 head is followed by one or
        more nominal-suffix tokens, both base and suffixes are consumed and
        replaced by a single _SyntheticToken whose surface == lemma == the
        concatenated form (e.g. 刑務所, 爆発的, 入院中的). Avoids unidic
        English-translation contamination in lemmas by setting lemma to the
        merged surface.
        """
        merged: list = []
        i, n = 0, len(tokens)
        while i < n:
            head = tokens[i]
            try:
                head_pos1 = head.feature.pos1
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if head_pos1 == "名詞":
                j = i + 1
                chain: list = []
                while j < n:
                    try:
                        p1 = tokens[j].feature.pos1
                        p2 = tokens[j].feature.pos2
                    except AttributeError:
                        break
                    if p1 == "接尾辞" and p2 in _NOMINAL_SUFFIX_POS2:
                        chain.append(tokens[j])
                        j += 1
                    else:
                        break
                if chain:
                    surf = head.surface + "".join(t.surface for t in chain)
                    try:
                        head_kana = head.feature.kana or head.surface
                    except AttributeError:
                        head_kana = head.surface
                    suffix_kanas = []
                    for t in chain:
                        try:
                            suffix_kanas.append(t.feature.kana or t.surface)
                        except AttributeError:
                            suffix_kanas.append(t.surface)
                    kana = head_kana + "".join(suffix_kanas)
                    try:
                        head_pos2 = head.feature.pos2 or "普通名詞"
                    except AttributeError:
                        head_pos2 = "普通名詞"
                    merged.append(
                        _SyntheticToken(
                            surface=surf,
                            pos1="名詞",
                            pos2=head_pos2,
                            lemma=surf,
                            kana=kana,
                        )
                    )
                    i = j
                    continue
            merged.append(head)
            i += 1
        return merged

    def _merge_prefix_compounds(self, tokens: list) -> list:
        """Merge 接頭辞 + 名詞/形状詞 pairs into a single token.

        Only fires when the 接頭辞 surface is in _PREFIX_WHITELIST — this
        avoids over-merging on rare/unproductive prefixes (e.g. お+金).
        Empirically: 不+可能 → root is 形状詞, 無+関心 → root is 名詞, so
        both pos1 values are accepted as merge heads. The synthetic is
        emitted as pos1=名詞 (the compound is treated as a vocabulary unit,
        and 名詞 is what _merge_noun_suffixes expects as a head — this
        enables chaining like 不+可能+性 → 不可能 → 不可能性). pos2 inherits
        from the root, defaulting to 普通名詞 when unidic emits "*".
        """
        merged: list = []
        i, n = 0, len(tokens)
        while i < n:
            head = tokens[i]
            try:
                head_pos1 = head.feature.pos1
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if head_pos1 == "接頭辞" and head.surface in _PREFIX_WHITELIST and i + 1 < n:
                root = tokens[i + 1]
                try:
                    root_pos1 = root.feature.pos1
                    raw_root_pos2 = root.feature.pos2
                except AttributeError:
                    merged.append(head)
                    i += 1
                    continue
                if root_pos1 in {"名詞", "形状詞"}:
                    # Treat unidic's "*" placeholder as missing pos2.
                    root_pos2 = (
                        raw_root_pos2 if raw_root_pos2 and raw_root_pos2 != "*" else "普通名詞"
                    )
                    surf = head.surface + root.surface
                    try:
                        head_kana = head.feature.kana or head.surface
                    except AttributeError:
                        head_kana = head.surface
                    try:
                        root_kana = root.feature.kana or root.surface
                    except AttributeError:
                        root_kana = root.surface
                    merged.append(
                        _SyntheticToken(
                            surface=surf,
                            pos1="名詞",
                            pos2=root_pos2,
                            lemma=surf,
                            kana=head_kana + root_kana,
                        )
                    )
                    i += 2
                    continue
            merged.append(head)
            i += 1
        return merged

    def _merge_verb_nominalizers(self, tokens: list) -> list:
        """Merge 動詞(連用形) + 接尾辞(名詞的) verb-stem nominalizers.

        Only fires when the suffix surface is in _VERB_NOMINALIZER_SUFFIXES
        ({方, 手, 様}). Crucially uses the verb's CONJUGATED surface
        (連用形, e.g. 言い/読み/生き) — NOT its lemma — so the merged form
        is 言い方 not 言う方. The synthetic is emitted as pos1=名詞,
        pos2=普通名詞 (the compound is nominalized).
        """
        merged: list = []
        i, n = 0, len(tokens)
        while i < n:
            head = tokens[i]
            try:
                head_pos1 = head.feature.pos1
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if head_pos1 == "動詞" and i + 1 < n:
                suffix = tokens[i + 1]
                try:
                    suf_pos1 = suffix.feature.pos1
                    suf_pos2 = suffix.feature.pos2
                except AttributeError:
                    merged.append(head)
                    i += 1
                    continue
                if (
                    suf_pos1 == "接尾辞"
                    and suf_pos2 == "名詞的"
                    and suffix.surface in _VERB_NOMINALIZER_SUFFIXES
                ):
                    surf = head.surface + suffix.surface
                    try:
                        head_kana = head.feature.kana or head.surface
                    except AttributeError:
                        head_kana = head.surface
                    try:
                        suf_kana = suffix.feature.kana or suffix.surface
                    except AttributeError:
                        suf_kana = suffix.surface
                    merged.append(
                        _SyntheticToken(
                            surface=surf,
                            pos1="名詞",
                            pos2="普通名詞",
                            lemma=surf,
                            kana=head_kana + suf_kana,
                        )
                    )
                    i += 2
                    continue
            merged.append(head)
            i += 1
        return merged

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

        # For words with kanji, always include (POS/subtype gates above apply).
        # Pure hiragana words (no kanji, not katakana) fall through and are rejected.
        return bool(has_kanji)
