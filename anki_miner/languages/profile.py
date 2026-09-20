"""LanguageProfile: the per-language seam the composition root injects.

Protocols and plain data only — no implementation, no heavy imports. Japanese
lives on unchanged in ``anki_miner.services``; ``languages/ja`` adapts it in
place (wrap-in-place, spec 3). This file is the single authority for these
types: later stages construct them, never redeclare them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from anki_miner.services.resource_catalog import ResourceSpec  # re-export; do NOT define a second one

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig, AudioSourceEntry  # noqa: F401

__all__ = [
    "AudioDefaults",
    "CaptionLangs",
    "CardFieldSpec",
    "CardRenderHook",
    "ContentTextStyle",
    "DictKeyFolding",
    "LanguageProfile",
    "LookupStrategy",
    "MinedFormPolicy",
    "PosDefaults",
    "ReadingSupport",
    "ResourceSpec",
    "ScriptFilterOption",
    "ScriptSupport",
    "SentenceAnnotator",
    "SentenceRules",
    "SubtitleParser",
]


class SubtitleParser(Protocol):
    """Surface EpisodeProcessor already consumes; ja = SubtitleParserService."""

    tagger: Any

    def parse_subtitle_file(self, *args: Any, **kwargs: Any) -> Any: ...
    def parse_subtitle_file_with_index(self, *args: Any, **kwargs: Any) -> Any: ...
    def parse_text_units(self, *args: Any, **kwargs: Any) -> Any: ...
    def count_lemmas(self, *args: Any, **kwargs: Any) -> Any: ...


class MinedFormPolicy(Protocol):
    """Card-front spelling. ja delegates to models.word.select_mined_form."""

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str: ...


class LookupStrategy(Protocol):
    """Ordered ``(candidate_text, conditions)`` fallbacks for a lookup miss.

    Mirrors ``DefinitionService._fallback_candidates`` (definition_service.py:217)
    exactly — see part 2 of the contract.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]: ...


class ReadingSupport(Protocol):
    """Word-level reading for the card's reading field. Profile field is optional."""

    def word_reading(self, token: Any) -> str: ...


class SentenceAnnotator(Protocol):
    """(annotated, plain) sentence pair — ja furigana. Profile field is optional."""

    def annotate_sentence(self, text: str, tokens: Any) -> tuple[str, str]: ...


@dataclass(frozen=True)
class ScriptFilterOption:
    """One toggle in the settings script-filter section.

    ``config_field`` is the AnkiMinerConfig boolean driving it, or ``""`` for an
    option with no field of its own.
    """

    option_id: str
    label: str
    config_field: str


class ScriptSupport(Protocol):
    def filter_options(self) -> tuple[ScriptFilterOption, ...]: ...
    def matches(self, option_id: str, form: str) -> bool: ...
    def contains_target_script(self, text: str) -> bool: ...


class DictKeyFolding(Protocol):
    """Import-time and query-time key folding + render-path homograph scope.

    ``homograph_keep_mask`` mirrors ``services/dictionary/storage.py:247``
    verbatim in arity and return.

    Two OPTIONAL methods an implementation may add, each probed by ``getattr``
    at its one reader so the other profiles need neither: ``term_variants(term)
    -> list[str]`` (read by ``IndexedFreqProvider``) and ``sense_rank(content)
    -> int``, the lookup sort's row demotion (read by
    ``storage._sense_rank_fn``). Both live on ``ZhDictKeyFolding``.
    """

    def fold_term(self, s: str) -> str: ...
    def fold_reading(self, s: str | None) -> str | None: ...
    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]: ...


class CardRenderHook(Protocol):
    """Non-ja extra card fields. ``field_names`` are LOGICAL anki_fields keys
    (like "frequency"/"glossary"), never Anki field names.

    ``render`` takes the running :class:`AnkiMinerConfig` keyword-only. Without
    it a language-scoped setting that only a hook can honour — zh's
    ``reading_tone_color`` — has nothing to reach: the field would exist,
    serialize and switch with the language while changing no output anywhere.
    Keyword-only so a hook cannot bind it to ``word`` by accident.
    """

    def field_names(self) -> tuple[str, ...]: ...
    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]: ...


@dataclass(frozen=True)
class SentenceRules:
    """Character classes for services/reading/sentence_splitter.py.

    Names and types mirror its module constants verbatim: ``_HARD_TERMINATORS``
    (:17), ``_ELLIPSIS`` (:21), ``_OPENERS`` (:25), ``_CLOSERS`` (:26).
    """

    terminators: frozenset[str]
    ellipses: frozenset[str]
    openers: frozenset[str]
    closers: frozenset[str]
    space_aware: bool = False
    #: Casefolded abbreviation keys WITHOUT their final dot (``dr``, ``z.b``); a
    #: multi-token abbreviation enters by each token (``p``, ``ej``). Non-empty
    #: also switches on the ASCII ellipsis rule (``...`` does not terminate);
    #: empty — ja, ko, zh — keeps every split the splitter made before Stage S.
    abbreviations: frozenset[str] = frozenset()
    #: A whitespace run at depth 0 ends the sentence (S9). Thai writes almost no
    #: terminator punctuation, so without this a chapter comes back as one
    #: sentence; the space IS its clause boundary. Reading-tab loaders only —
    #: subtitle cues are already sentences. Terminators still apply.
    split_on_whitespace: bool = False


@dataclass(frozen=True)
class AudioDefaults:
    """Expression/sentence audio per language.

    ``default_chain`` is the value scoped_defaults["expression_audio_chain"]
    carries — real ``AudioSourceEntry`` objects, not tuples.
    ``candidates`` builds the ``fetch_candidates`` ladder: ``(term, reading)``
    pairs, matching ExpressionAudioFetcher.fetch_candidates' real parameter
    (``candidates: list[tuple[str, str]]``, expression_audio_fetcher.py:327/:400).
    TWO distinct stem prefixes, because the word and sentence caches are
    separate files with separate literals — one prefix cannot serve both:
    ``cache_stem_prefix`` replaces the literal "googletts" in the WORD-audio
    stem (``google_translate_audio_fetcher.py:225``,
    ``stem=safe_filename(f"googletts_{mined_form}_{reading}")``), and
    ``sentence_cache_stem_prefix`` replaces the literal "sentencetts" in the
    SENTENCE stem (``sentence_tts_fetcher.py:82-86``,
    ``_sentence_stem(provider, sentence) -> f"sentencetts_{provider}_{digest}"``,
    called at :132 and :180). Both fetchers take their prefix at construction
    from ``service_factory``; no language-code branch lives outside
    ``languages/`` (there is no ``service_factory.sentence_cache_stem_prefix``).
    """

    #: gTTS language code, or a callable resolving it from the running config
    #: (pt picks ``pt``/``pt-PT`` by variety). "" means the language has no
    #: Google voice: service_factory builds neither Google leg.
    gtts_lang: str | Callable[[AnkiMinerConfig], str]
    cache_stem_prefix: str
    sentence_cache_stem_prefix: str
    custom_fetcher_language: str
    papago_speaker: str | None = None
    default_chain: tuple[AudioSourceEntry, ...] = ()
    candidates: Callable[[Any], list[tuple[str, str]]] | None = None
    #: What the synthetic and custom word-audio sources may speak for a
    #: ``(term, reading)`` ladder pair: the text, or None to skip the pair (R5).
    #: ``None`` is Japanese — the reading, only when it is kana (a kanji reading
    #: is the tokenizer's OOV fallback and would let the voice guess a homograph).
    #: JPod101 keeps its own kana gate: its endpoint only answers Japanese.
    speakable: Callable[[str, str], str | None] | None = None
    #: The Microsoft Edge read-aloud voice the ``edgetts`` word-audio kind
    #: speaks with — the service's short name (``fa-IR-DilaraNeural``). "" means
    #: the language has no Edge voice: service_factory builds no Edge leg and
    #: Settings -> Audio does not offer one. A language with no Google voice
    #: (``gtts_lang == ""``) names one and puts ``AudioSourceEntry(kind="edgetts")``
    #: in ``default_chain`` (spec D14, tests/unit/languages/test_edge_voice_contract.py).
    edge_voice: str = ""

    def resolved_gtts_lang(self, config: AnkiMinerConfig) -> str:
        """The gTTS code for *config*; "" when the language has no Google voice."""
        return self.gtts_lang(config) if callable(self.gtts_lang) else self.gtts_lang


@dataclass(frozen=True)
class CaptionLangs:
    """yt-dlp caption + audio-track parameters (services/youtube_fetcher.py).

    ``codes`` is the full ordered request list: the joined --sub-lang value and
    the preference order a fetch's output files are resolved by. ``own_codes``
    is the subset naming the language's *own* track — what the probe's manual
    and native-auto gates read — and empty means every entry in ``codes`` does.
    Only a profile that lists another language's codes as a fetch fallback (yue
    falls back to written Chinese) has to narrow it, or a video subtitled in
    that other language would be taken for one in this one. ``primary`` is one
    of ``codes`` and the one the auto-dub relaxation keys on; ``orig_codes``
    are the ASR-native marker keys; ``audio_pattern`` is the format-selector
    regex body; ``bare_fallback`` allows accepting the bare code when no -orig
    exists.
    """

    primary: str
    codes: tuple[str, ...]
    orig_codes: tuple[str, ...]
    audio_pattern: str
    bare_fallback: bool = False
    own_codes: tuple[str, ...] = ()

    @property
    def accepted_codes(self) -> tuple[str, ...]:
        """The codes whose presence proves the video carries this language."""
        return self.own_codes or self.codes


@dataclass(frozen=True)
class PosDefaults:
    """POS gate defaults and human labels for a language's tagset.

    ``labels`` has no consumer yet (S20): the settings POS editor still shows
    raw tags. It is populated so a label-aware editor is a later, cheap change.
    """

    allowed_pos: tuple[str, ...]
    excluded_subtypes: tuple[str, ...]
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CardFieldSpec:
    """One extra logical card field a language's render hooks add.

    ``key`` is the logical ``anki_fields`` key (matches a hook's
    ``field_names()`` entry). ``capability`` names the profile capability that
    gates surfacing this field in the UI — must be in ``CAPABILITY_VOCABULARY``
    (test_language_contract.py) and in the owning profile's own
    ``capabilities``. ``placeholder`` is an untranslated Anki field-name
    suggestion, never shown as-is without going through i18n at the call site.
    ``raw_html`` marks a value that is pre-rendered markup, inserted verbatim
    instead of html.escape()d: ``AnkiService`` feeds these keys to
    ``build_note`` as ``extra_raw_html_keys``. The ja/ko/zh keys are already in
    ``services/anki_note_builder.py::_RAW_HTML_FIELD_KEYS``, which is frozen —
    a later language's key is carried by this flag alone.
    """

    key: str
    capability: str
    placeholder: str
    raw_html: bool = False


@dataclass(frozen=True)
class ContentTextStyle:
    """Typography for surfaces displaying MINED CONTENT (not chrome).

    ``font_role`` == "japanese" routes to gui/utils/fonts.py's existing helpers
    byte-identically. ``families`` is the ordered candidate face list.
    ``wrap`` is the soft-wrap transform; ja == phrase_wrap.phrase_wrap_ja.

    ``direction``, ``writing_system`` and ``bundled_fallback`` are the S21/S22
    seam; their defaults are the pre-seam behaviour, so a profile that sets none
    of them is unchanged.
    ``direction`` ("ltr" | "rtl") flips the content widgets and wraps the card's
    word and sentence fields (gui/utils/content_text.py, anki_note_builder).
    ``writing_system`` is a ``QFontDatabase.WritingSystem`` member name
    ("Arabic", "Hebrew", "Thai"); when set, ``families`` is probed against the
    faces installed for that script. ``bundled_fallback`` is a face basename under
    gui/resources/fonts/, registered only when that probe finds none
    (gui/utils/fonts.py::resolve_content_families).

    ``card_lang`` answers the BCP-47 tag the card's sentence declares, given the
    card front and the config; ``""`` means "write no tag", and ``None`` means
    the language never writes one, which is the pre-existing note. It is a
    resolver rather than a string because the tag can depend on the setting
    (zh's Character Set) and, when that is unset, on the word itself.
    """

    font_role: str
    families: tuple[str, ...]
    wrap: Callable[[str], str]
    direction: str = "ltr"
    writing_system: str = ""
    bundled_fallback: str = ""
    card_lang: Callable[[str, AnkiMinerConfig], str] | None = None


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    display_name: str
    create_parser: Callable[..., SubtitleParser]
    mined_form: MinedFormPolicy
    lookup: LookupStrategy
    reading: ReadingSupport | None
    sentence_annotator: SentenceAnnotator | None
    script: ScriptSupport
    audio_track_codes: frozenset[str]
    import_encodings: tuple[str, ...]
    scoped_defaults: Mapping[str, object]
    sentence_rules: SentenceRules
    normalize: Callable[[str], str]
    dict_keys: DictKeyFolding
    audio: AudioDefaults
    asr_language: str
    captions: CaptionLangs
    pos_defaults: PosDefaults
    catalog: tuple[ResourceSpec, ...]
    capabilities: frozenset[str]
    card_field_defaults: Mapping[str, str]
    render_hooks: tuple[CardRenderHook, ...]
    content_style: ContentTextStyle
    #: Why this language cannot mine on THIS machine, or ``None`` when it can.
    #: Optional like ``reading``/``sentence_annotator``, and last because it is
    #: the only defaulted field - every profile that has nothing optional to
    #: report leaves it unset. The probe runs at call time (zh answers from
    #: ``find_spec``), never at construction: the profile itself always builds.
    unavailable_reason: Callable[[], str | None] | None = None
    #: Extra logical fields this language's render hooks add, for UI surfaces
    #: (field mapping pickers) that need to describe them without importing the
    #: hooks. Trailing + defaulted like the two below: ``dataclasses.replace``
    #: and positional construction of an existing profile keep working.
    extra_card_fields: tuple[CardFieldSpec, ...] = ()
    #: One in-language sentence for ``ANKI_MINER_SMOKE=<code>`` bundle smokes
    #: and any UI preview. Copied verbatim from ``gui/app.py`` —
    #: ``_LANGUAGE_SMOKE_LINES`` is not imported here (this module stays a
    #: Qt-free leaf); each language package owns its own literal.
    smoke_sentence: str = ""
    #: English display name, for surfaces that cannot render the native script
    #: (log lines, ASCII-only widgets). ``display_name`` stays the native form.
    english_name: str = ""
    #: The en.wiktionary section / wty edition code when it differs from
    #: ``code`` (R28: hr reads ``sh``). "" means ``code``. Consumers: the
    #: dictionary import's sourceLanguage note (S19) and, from Stage W, the
    #: Wiktionary REST provider's section pick.
    wiktionary_code: str = ""
    #: The known-words comparison fold (R6, S3): applied to every stored and
    #: probed form at the known-words DB, the Anki vocabulary boundary, the word
    #: filter, the word lists and the Deck Builder preview. Must be idempotent.
    #: ``None`` — ja, ko — is ``normalize_lemma`` (NFC) at the DB and the
    #: pre-seam raw comparison everywhere else. Deliberately a different
    #: function from ``dict_keys.fold_term`` (R7): index keys are never
    #: article-stripped.
    dedup_fold: Callable[[str], str] | None = None
