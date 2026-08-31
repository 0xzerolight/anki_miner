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

    gtts_lang: str
    cache_stem_prefix: str
    sentence_cache_stem_prefix: str
    custom_fetcher_language: str
    papago_speaker: str | None = None
    default_chain: tuple[AudioSourceEntry, ...] = ()
    candidates: Callable[[Any], list[tuple[str, str]]] | None = None


@dataclass(frozen=True)
class CaptionLangs:
    """yt-dlp caption + audio-track parameters (services/youtube_fetcher.py).

    ``primary`` is the single code probed in ``subs``/``automatic_captions``
    (:169, :393) and used as the --sub-lang value; ``orig_codes`` are the ASR-
    native marker keys (:396); ``codes`` is the full ordered request list;
    ``audio_pattern`` is the format-selector regex body (:577, :410);
    ``bare_fallback`` allows accepting the bare code when no -orig exists.
    """

    primary: str
    codes: tuple[str, ...]
    orig_codes: tuple[str, ...]
    audio_pattern: str
    bare_fallback: bool = False


@dataclass(frozen=True)
class PosDefaults:
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
    """

    font_role: str
    families: tuple[str, ...]
    wrap: Callable[[str], str]


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
