"""Data models for vocabulary words."""

from dataclasses import dataclass, field
from pathlib import Path

from anki_miner.models.media import MediaData

# Colloquial vowel-elongation tail characters a 名詞 surface can carry over its
# lemma (手ぇ, 気い, 目ー): small kana vowels, full kana vowels and the long-vowel
# mark. Restricted to these so only a genuine drawn-out reading folds — an ordinary
# okurigana/compound tail (パン→パンダ) never matches.
_VOWEL_ELONGATION_TAIL = frozenset("ぁぃぅぇぉあいうえおー")


def select_mined_form(pos: str | None, orth_base: str, lemma: str, surface: str) -> str:
    """Single selection rule for the card-front form.

    Shared by ``TokenizedWord.mined_form`` and the parser's ``_emit_word``
    (which needs the value before the word object exists, to derive
    expression furigana/reading). Keep it the only place this rule lives —
    a drifted copy silently splits the Expression field from the
    dedup/known-words/audio identity.

    Nouns whose surface is the lemma plus a 1-2 char colloquial vowel-elongation
    tail (手ぇ→手, 気い→気) fold to the lemma so the card dedups against the plain
    form. String-only: the ``surface.startswith(lemma)`` guard keeps Issue #5
    homographs (豪腕/剛腕, surface does not start with the variant lemma) on the
    surface, and only ``_VOWEL_ELONGATION_TAIL`` chars qualify. コーヒー is
    unaffected (its gloss-stripped lemma equals the surface). Any ``known_words``
    rows keyed on the old 手ぇ front go dead — benign, no migration.
    """
    if pos in ("動詞", "形容詞"):
        return orth_base or lemma
    if pos == "名詞" and lemma and surface != lemma and surface.startswith(lemma):
        tail = surface[len(lemma) :]
        if 1 <= len(tail) <= 2 and all(c in _VOWEL_ELONGATION_TAIL for c in tail):
            return lemma
    return surface


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
    # Mining base: the dictionary form in the sentence's own orthography
    # (UniDic orthBase): 乞わ → 乞う even when unidic's canonical lemma is
    # 請う — folded to the lemma when orthBase is a derived sub-lemma
    # (potential 保てる→保つ, ra-nuki 見れる→見る, adjective ク-form
    # 良し→良い; see morphology.mining_base for the reading trigger and the
    # suffix-pair guard that keeps kanji/okurigana/じる-ずる variants
    # unfolded). Card-front source of truth for verbs/adjectives; empty when
    # the token had no orthBase (synthetic merged compounds, OOV) —
    # mined_form then falls back to lemma.
    orth_base: str = ""
    expression_furigana: str = ""  # Furigana for expression, e.g. "食べる[たべる]"
    expression_reading: str = ""  # Plain kana reading of expression, e.g. "たべる"
    lemma_reading: str = ""  # Plain kana reading of the lemma, for audio retry
    # Kana reading of the resolved card front when the JMdict verb-front resolver
    # overrode ``orth_base`` (感じた: front 感じる / reading かんじる) — see
    # deinflection.resolve_dictionary_form. Empty when no override fired. Pitch
    # is otherwise lemma-reading-keyed, but the じる/ずる override diverges the
    # front's reading (かんじる) from the archaic lemma's own (感ずる→かんずる),
    # so the pitch sites prefer this field over ``lemma_reading`` when it is set.
    resolved_reading: str = ""
    sentence_furigana: str = ""  # Furigana for sentence, e.g. "日本語[にほんご]を食べる[たべる]。"
    sentence_reading: str = ""  # Plain kana reading of sentence, e.g. "にほんごをたべる。"
    frequency_rank: int | None = None  # Word frequency rank (1 = most common); = min across sources
    # Per-source frequency breakdown shown on the card:
    # (source name, rank, display_value) in chain order, only sources that rank
    # this word. ``display_value`` is the human string a card shows in place of
    # the bare rank (Yomitan displayValue; None for plain-int/CSV ranks or a v1
    # index). ``frequency_rank`` stays the min of the ranks (drives
    # filtering/sort); this is the display detail.
    frequency_sources: list[tuple[str, int, str | None]] = field(default_factory=list)
    # Harmonic mean of the per-source ranks (Yomitan getFrequencyHarmonic); backs
    # the numeric ``frequency_sort`` card field. None when no source ranks the
    # word (card writes the 9999999 "missing" sentinel so it sorts last).
    frequency_harmonic_rank: int | None = None
    # Times this word's lemma occurs in the current episode. Display/sort-only,
    # attached on the interactive curation path (Issue #88); 0 when not computed.
    occurrence_count: int = 0
    pos: str | None = None  # MeCab pos1 (動詞/形容詞/名詞/...) — used for kifuku/odaka distinction
    # Character offsets of the target morpheme within ``sentence`` (post-filter).
    # -1 sentinel means "not tracked" — card builder falls back to plain escape.
    # Invariant: sentence[surface_start:surface_end] == surface (the Issue #20
    # offset-drift canary) — do NOT widen these to the inflected form.
    surface_start: int = -1
    surface_end: int = -1
    # End offset of the FULL inflected form (verb/adjective + auxiliary chain,
    # Yomitan-deinflection-verified): 蒔いた bolds fully instead of just the
    # stem morpheme 蒔い. -1 sentinel means "same as surface_end". Bolding
    # spans [surface_start, bold_end); extension is strictly rightward.
    highlight_end: int = -1
    # Precomputed bolded variants of sentence / sentence_furigana with
    # <b>...</b> wrapping the target morpheme. Populated at parse time
    # (or i+1 swap time) only when config.bold_target_in_sentence is on.
    # Empty string means "not precomputed" — card builder falls back to escape.
    sentence_bolded: str = ""
    sentence_furigana_bolded: str = ""
    # Alternative example sentences for this word — one fully-swapped variant
    # per subtitle line the lemma appears on (built by
    # WordFilterService.attach_sentence_candidates from the parse line index).
    # Includes the current pick, so a non-empty list always holds >= 2 entries.
    # Empty ⇒ the word appears on a single line / candidates not attached, so
    # the curator shows no sentence picker. Each entry is a leaf: its own
    # sentence_candidates stays empty (no recursion).
    sentence_candidates: list["TokenizedWord"] = field(default_factory=list)

    @property
    def bold_end(self) -> int:
        """End offset for bold wrapping: ``highlight_end`` when tracked,
        else ``surface_end`` (single shared fallback rule)."""
        return self.highlight_end if self.highlight_end >= 0 else self.surface_end

    @property
    def mined_form(self) -> str:
        """The form that becomes the card front (Expression field).

        Verbs and adjectives mine as the dictionary form so that ``破れ``
        becomes ``破れる`` — the learner studies the form that
        recognizes/produces every conjugation (Issue #19). The dictionary
        form used is ``orth_base`` (source orthography), NOT ``lemma``:
        unidic's canonical lemma silently swaps kanji variants
        (乞う→請う, 喰らう→食らう) and the card must keep the spelling the
        sentence actually used. Yomitan behaves the same way — it
        deinflects the raw string and never normalizes to a canonical
        headword. ``lemma`` remains the fallback when ``orth_base`` is
        empty. Definition and frequency lookups also key on ``mined_form``
        (with a miss-only ``lemma`` fallback) so the fetched data matches
        the spelling the card shows — 殺る must not get 遣る's "to do"
        definition or 掛ける's rank; only pitch stays lemma-keyed
        (variants share the reading, canonical orthography has the better
        hit rate in reading-scoped pitch CSVs). The one exception: when the
        JMdict verb-front resolver overrides ``orth_base`` (感じた: 感ずる →
        感じる), the front's reading (かんじる) diverges from the archaic
        lemma's own (感ずる→かんずる), so ``resolved_reading`` carries the
        front reading and the pitch sites prefer it over ``lemma_reading``.
        Kana-surface verbs never reach mining (TokenInclusionRule requires
        kanji or katakana), so orthBase-vs-lemma only ever differs on
        kanji-surface variant tokens. Verbs carded before this change
        stored the normalized lemma and will re-card once as the source
        variant (accepted, no migration — see CHANGELOG).

        ``orth_base`` arrives pre-folded by ``morphology.mining_base``:
        derived sub-lemma entries (potential 保てる, ra-nuki 見れる,
        adjective ク-form 良し) collapse onto their parent lemma
        (保つ/見る/良い) so they dedup against the base-form card. The fold
        boundary is unidic's classification — 思える/起きれる fold to
        思う/起きる while lexicalized 見える/聞こえる/できる keep their own
        entries — and the suffix-pair guard keeps every lemma
        canonicalization unfolded (kanji swaps 帰れる→返る/出逢える→出会う,
        okurigana variants 表せる→表わす, modern→archaic 信じる→信ずる all
        mine their source orthBase). Stem tokens like 信じ (from
        信じられない) still mine 信ずる — pre-existing quirk, readings
        equal, never triggers the fold. Cards mined as potential forms
        before this change get a base-lemma sibling next time the word
        recurs (one-time re-card burst, broader than the orthBase
        precedent above; accepted — see CHANGELOG).

        Nouns and other non-conjugating POS keep the surface form: unidic
        sometimes maps homograph-like nouns to a different headword
        (``豪腕`` → ``剛腕``); preserving surface for nouns avoids that
        regression (Issue #5). The one carve-out: a 名詞 surface that is the
        lemma plus a short colloquial vowel-elongation tail (``手ぇ`` → ``手``,
        ``気い`` → ``気``) folds to the lemma — see ``select_mined_form`` for the
        string-only rule and its guards.
        """
        return select_mined_form(self.pos, self.orth_base, self.lemma, self.surface)

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
    # Per-lemma (lemma, surface, start, end, highlight_end) for each content
    # lemma's first appearance on this line. Used by the i+1 sentence filter
    # to bold the correct span after swapping the sentence to a different
    # line; highlight_end covers the full inflected form (-1 = same as end).
    # Tuple-of-tuples instead of dict to keep the dataclass frozen.
    lemma_spans: tuple[tuple[str, str, int, int, int], ...] = field(default_factory=tuple)


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
