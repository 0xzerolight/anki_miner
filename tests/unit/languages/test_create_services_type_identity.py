"""Spec 13 item 6: create_services wires the same concrete classes for ja.

Task 1A.12, the last task of Stage 1A: the proof that the whole profile seam is
invisible for Japanese. Every earlier task turned a hard-coded Japanese decision
into an injected one; a profile-driven factory can therefore silently swap a
concrete class, or — the failure mode this plan is built around — silently
*stop* passing a keyword and leave a dead field nobody notices.

Two halves, both cheap:

* Type identity. ``type(x) is C``, never ``isinstance``: a subclass must fail,
  because a subclass is exactly what a mis-wired profile would hand back. Every
  ``Services`` field is classified, so a field added later cannot slip through
  unchecked.
* Construction shape. The stage's omit-when-ja rulings, pinned from both sides:
  ja passes NO ``lookup`` to ``DefinitionService`` and no ``enabled_options`` to
  the script filter, takes its tagger from the ja shared tagger and opens the
  configured known-words path verbatim; a non-ja profile receives every one of
  those keywords instead.

The non-ja side runs against a stub profile (controller ruling R6): a
``dataclasses.replace`` of the real ja profile behind a ``get_profile``
monkeypatch, plus a ``monkeypatch.setitem`` registry entry — ``config_language``
degrades a code with no registered profile to ja before ``get_profile`` is
reached, and ``setitem`` is what keeps the entry from leaking into later tests
(a plain registration would, since the registry caches per code). The pattern
(including seeding the
process-wide tagger cache, which ``SubtitleParserService`` reaches for on the
non-ja branch) is ``test_lookup_strategy_dispatch._use_stub_profile``; this file
generalizes it to override three profile fields at once and to seed a *distinct*
tagger object, which is what makes the tagger assertion non-vacuous.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import service_factory
from anki_miner.gui.utils.service_factory import Services, create_services
from anki_miner.languages import tagger_provider
from anki_miner.languages.registry import get_profile
from anki_miner.languages.zh.parser import create_parser as zh_create_parser
from anki_miner.languages.zh.reading import ZhReadingSupport
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.expression_audio_fetcher import ChainedExpressionAudioFetcher
from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.pitch_accent.multi_pitch_service import MultiPitchAccentService
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry
from anki_miner.services.sentence_tts_fetcher import ChainedSentenceAudioFetcher
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.tagger import get_shared_tagger
from anki_miner.services.word_filter import WordFilterService, enabled_script_options, script_options_kwarg
from anki_miner.services.word_list_service import WordListService
from anki_miner.services.wordset_service import WordsetService
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from tests._home_isolation import restore_home_patches, set_test_home

ALWAYS_BUILT = {
    "subtitle_parser": SubtitleParserService,
    "word_filter": WordFilterService,
    "media_extractor": MediaExtractorService,
    "definition_service": DefinitionService,
    "anki_service": AnkiService,
    "youtube_fetcher": YouTubeFetcherService,
    "expression_audio_fetcher": ChainedExpressionAudioFetcher,
    "sentence_audio_fetcher": ChainedSentenceAudioFetcher,
    "dictionary_registry": DictionaryRegistry,
}
OPTIONAL = {
    "pitch_accent_service": MultiPitchAccentService,
    "frequency_service": MultiFrequencyService,
    "known_word_db": KnownWordDB,
    "word_list_service": WordListService,
    "wordset_service": WordsetService,
    "frequency_registry": FrequencySourceRegistry,
    "pitch_registry": PitchSourceRegistry,
    "audio_pack_registry": AudioPackRegistry,
}


def test_ja_builds_exactly_these_concrete_classes(tmp_path, monkeypatch):
    saved = set_test_home(tmp_path / "home")
    try:
        services = create_services(AnkiMinerConfig())
    finally:
        restore_home_patches(saved)
    assert services.subtitle_parser.tagger is get_shared_tagger()
    for name, cls in ALWAYS_BUILT.items():
        assert type(getattr(services, name)) is cls, name
    for name, cls in OPTIONAL.items():
        value = getattr(services, name)
        assert value is None or type(value) is cls, name


def test_every_services_field_is_covered():
    """A new Services field must be classified, not silently unchecked."""
    names = {f.name for f in dataclasses.fields(Services)}
    assert names == set(ALWAYS_BUILT) | set(OPTIONAL) | {"load_result"}


# ---------------------------------------------------------------------------
# Construction shape: the omit-when-ja rulings, from both sides
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    """Default config whose every path resolves under an isolated home.

    Built AFTER ``set_test_home`` so the dataclass' ``ANKI_MINER_HOME``-derived
    defaults (``known_words_db_path``, ``dicts_root``, ...) point at the tmp
    home — this run opens and initializes real files, and must never reach the
    genuine ``~/.anki_miner``.
    """
    saved = set_test_home(tmp_path / "home")
    try:
        yield AnkiMinerConfig()
    finally:
        restore_home_patches(saved)


class _StubLookup:
    """Distinct LookupStrategy — ``_lookup_kwarg`` gates on object identity."""

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        return []


class _StubMinedForm:
    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return surface


class _StubScript:
    def filter_options(self) -> tuple:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return False


def _use_stub_profile(monkeypatch, code: str, **overrides):
    """Route *code* to a replaced copy of the ja profile; return it + its tagger.

    Ruling R6: ``dataclasses.replace`` off the REAL ja profile, so every field
    the factory reads and this test does not care about stays a working one, and
    monkeypatched onto ``service_factory`` rather than registered — the registry
    caches per code and a registration would outlive the test.

    The tagger cache entry is the 1A.6 dependency: ``SubtitleParserService``
    takes a non-ja tagger from ``languages.tagger_provider``, which raises for a
    code with no tokenizer. Seeded with a sentinel (not the ja tagger) so the
    parser's tagger assertion actually distinguishes the two branches;
    ``monkeypatch.setitem`` drops it again at teardown.

    The registry entries are ``monkeypatch.setitem`` too, and for the same
    reason they are dropped at teardown: ``config_language`` degrades a code
    with no registered profile to ja before ``get_profile`` is reached.
    """
    from anki_miner.languages import registry

    stub_profile = dataclasses.replace(get_profile("ja"), code=code, **overrides)
    real_get_profile = service_factory.get_profile

    def fake_get_profile(requested: str):
        return stub_profile if requested == code else real_get_profile(requested)

    monkeypatch.setattr(service_factory, "get_profile", fake_get_profile)
    monkeypatch.setitem(registry._BUILDERS, code, lambda: stub_profile)
    monkeypatch.setitem(registry._CACHE, code, stub_profile)
    tagger = MagicMock(name=f"tagger-{code}")
    monkeypatch.setitem(tagger_provider._TAGGERS, code, tagger)
    return stub_profile, tagger


def _record(monkeypatch, name: str) -> MagicMock:
    """Wrap a class the factory constructs, keeping the real object it returns."""
    recorder = MagicMock(wraps=getattr(service_factory, name))
    monkeypatch.setattr(service_factory, name, recorder)
    return recorder


def test_ja_construction_passes_no_language_keywords(config, monkeypatch):
    """Japanese keeps the pre-transition construction shape, keyword for keyword.

    ``lookup=None`` already IS the ja candidate ladder, so passing the ja
    strategy would be a no-op that nonetheless breaks the call shape pre-existing
    tests pin. The word filter is the other half of the same ruling: it takes the
    profile's policies, but nothing about the *call* changes for ja.
    """
    ja = get_profile("ja")
    definition_cls = _record(monkeypatch, "DefinitionService")
    filter_cls = _record(monkeypatch, "WordFilterService")

    services = create_services(config)

    assert set(definition_cls.call_args.kwargs) == {"providers", "registry"}
    assert services.definition_service._lookup is None
    assert filter_cls.call_args.kwargs["mined_form"] is ja.mined_form
    assert filter_cls.call_args.kwargs["script"] is ja.script


def test_ja_parser_and_filter_share_the_ja_shared_tagger(config):
    """One tagger for the run, and it is the ja shared one.

    ``fugashi.Tagger`` init is expensive: the filter borrows the parser's tagger
    rather than building a second. Both must be the process-wide ja instance —
    the non-ja provider branch must not fire for Japanese.
    """
    services = create_services(config)

    assert services.subtitle_parser.tagger is get_shared_tagger()
    assert services.word_filter.tagger is services.subtitle_parser.tagger


def test_ja_known_word_db_opens_the_configured_path_verbatim(config):
    """Same file, same bytes: ja never gets a per-language sibling."""
    services = create_services(config)

    assert services.known_word_db._db_path == config.known_words_db_path


def test_ja_script_filtering_splats_no_enabled_options(config):
    """The ja filter call omits ``enabled_options`` and re-derives it internally.

    ``enabled_options`` is a CALL-time keyword on ``filter_by_script_type``
    (splatted by ``episode_processor._phase2_filter`` and by ``deck_filter``),
    not a constructor argument. For ja the splat is empty, so the pre-extraction
    two-boolean call survives verbatim; that the omitted-keyword path re-derives
    exactly this option set is pinned in ``test_script_support.py``. What is
    pinned here is that the ja splat is empty while a non-ja one is not — an
    always-empty splat would be a dead field.
    """
    filtering = dataclasses.replace(config, exclude_hiragana_only_words=True)
    options = enabled_script_options(get_profile("ja").script, filtering)

    assert options == frozenset({"hiragana_only"})
    assert script_options_kwarg(options, "ja") == {}
    assert script_options_kwarg(options, "zh") == {"enabled_options": options}


def test_non_ja_construction_receives_every_language_keyword(config, monkeypatch):
    """The mirror image: nothing the ja path omits is unreachable.

    An unpassed keyword is a silently dead field — the known failure mode this
    whole seam exists to catch — so each omission is proved to be a shortcut for
    ja rather than a deletion. Driving ``create_services`` (not just
    ``build_definition_service``) also closes 1A.10's gap: that task could only
    pin the factory's known-words site by reading the source, since no non-ja
    profile existed to run it with.
    """
    profile, tagger = _use_stub_profile(
        monkeypatch,
        "zh",
        lookup=_StubLookup(),
        mined_form=_StubMinedForm(),
        script=_StubScript(),
    )
    zh_config = dataclasses.replace(config, language="zh")
    definition_cls = _record(monkeypatch, "DefinitionService")
    filter_cls = _record(monkeypatch, "WordFilterService")

    services = create_services(zh_config)

    assert definition_cls.call_args.kwargs["lookup"] is profile.lookup
    assert services.definition_service._lookup is profile.lookup
    assert filter_cls.call_args.kwargs["mined_form"] is profile.mined_form
    assert filter_cls.call_args.kwargs["script"] is profile.script
    assert services.subtitle_parser.tagger is tagger
    assert services.word_filter.tagger is tagger
    assert services.known_word_db._db_path == config.known_words_db_path.with_name("known_words.zh.db")


#: The six probe keywords ``create_services`` has always splatted into the
#: parser. Spelled out so an added-but-unpassed keyword, or a keyword the
#: profile seam starts injecting for ja, shows up as a diff here.
JA_PARSER_KEYWORDS = {
    "term_lookup",
    "name_lookup",
    "reading_lookup",
    "kana_attest_lookup",
    "term_common_lookup",
    "term_rules_lookup",
}


def test_ja_parser_is_built_with_the_pre_transition_call_shape(config, monkeypatch):
    """Japanese still constructs the parser class this module imports, verbatim.

    The parser is the one service whose construction a pre-existing test spies
    on through this module's attribute
    (``test_batch_queue_worker.test_one_subtitle_parser_service_for_the_whole_queue``
    patches ``service_factory.SubtitleParserService`` and counts the builds), so
    the ja branch of ``_create_subtitle_parser`` must keep reaching that global
    rather than the profile's factory. Config positional, six probe keywords,
    no policy keywords: ja's ``mined_form_policy=None`` / ``reading_support=None``
    defaults ARE today's Japanese behaviour and the drift canary pins them.
    """
    parser_cls = _record(monkeypatch, "SubtitleParserService")

    services = create_services(config)

    assert type(services.subtitle_parser) is SubtitleParserService
    assert parser_cls.call_args.args == (config,)
    assert set(parser_cls.call_args.kwargs) == JA_PARSER_KEYWORDS
    assert services.subtitle_parser._mined_form_policy is None
    assert services.subtitle_parser._reading_support is None


def test_non_ja_parser_is_built_by_the_profile_factory(config, monkeypatch):
    """A zh run gets the zh factory's parser, policies and all.

    The mirror of the test above, and the reason the seam exists: built as ja
    is, a Chinese parser would take its reading from ``feature.kana`` — empty on
    every ``LanguageToken`` — and its card front from the Japanese
    ``select_mined_form``, silently. The stub carries the REAL zh pieces
    (``zh.parser.create_parser``, ``ZhReadingSupport``) because
    ``zh.build_profile`` only registers at 2A.12; the mined-form policy is a
    stub until 2A.7 creates the zh one.
    """
    profile, tagger = _use_stub_profile(
        monkeypatch,
        "zh",
        create_parser=zh_create_parser,
        reading=ZhReadingSupport(),
        mined_form=_StubMinedForm(),
    )
    zh_config = dataclasses.replace(config, language="zh")

    services = create_services(zh_config)

    assert type(services.subtitle_parser) is SubtitleParserService
    assert type(services.subtitle_parser._reading_support) is ZhReadingSupport
    assert services.subtitle_parser._reading_support is profile.reading
    assert services.subtitle_parser._mined_form_policy is profile.mined_form
    assert services.subtitle_parser.tagger is tagger


def test_non_ja_builds_the_same_concrete_classes(config, monkeypatch):
    """The seam injects behaviour, never a different class.

    A profile that swapped a concrete service would defeat every type assertion
    above the moment a second language ships; the factory owns the classes, the
    profile owns only what they are configured with.
    """
    _use_stub_profile(monkeypatch, "zh", lookup=_StubLookup())
    zh_config = dataclasses.replace(config, language="zh")

    services = create_services(zh_config)

    for name, cls in ALWAYS_BUILT.items():
        assert type(getattr(services, name)) is cls, name
    for name, cls in OPTIONAL.items():
        value = getattr(services, name)
        assert value is None or type(value) is cls, name
