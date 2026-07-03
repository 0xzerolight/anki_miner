"""Tests for DefinitionService — chain walking over injected providers."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.services.definition_service import DefinitionService, collect_dictionary_css
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)


def _seed_dict(root: Path, dict_id: str, source_name: str, *, styles_css: str | None = None) -> None:
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, [DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', sequence=1)])
    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "source_name": source_name,
        "format": "yomitan",
        "entry_count": "1",
    }
    if styles_css is not None:
        meta["styles_css"] = styles_css
    write_meta(db, meta)


def _config(root: Path, *entries: ChainEntry) -> AnkiMinerConfig:
    return replace(AnkiMinerConfig(), dicts_root=root, dictionary_chain=entries)


class TestCollectDictionaryCss:
    """``collect_dictionary_css`` is the Yomitan ``_getCustomCss`` analog: each
    enabled indexed dict's scoped ``styles.css`` concatenated in chain order."""

    def test_empty_for_no_chain(self, tmp_path: Path):
        assert collect_dictionary_css(_config(tmp_path)) == ""

    def test_concatenates_scoped_css_in_chain_order(self, tmp_path: Path):
        _seed_dict(tmp_path, "a-dict", "A", styles_css="span { color: red }")
        _seed_dict(tmp_path, "b-dict", "B", styles_css="span { color: blue }")
        css = collect_dictionary_css(
            _config(
                tmp_path,
                ChainEntry(kind="indexed", dict_id="a-dict", enabled=True),
                ChainEntry(kind="indexed", dict_id="b-dict", enabled=True),
            )
        )
        # Each scoped to its own [data-dictionary]; A precedes B (chain order).
        assert '[data-dictionary="A"]' in css
        assert '[data-dictionary="B"]' in css
        assert css.index('[data-dictionary="A"]') < css.index('[data-dictionary="B"]')

    def test_skips_disabled_dict(self, tmp_path: Path):
        _seed_dict(tmp_path, "a-dict", "A", styles_css="span { color: red }")
        _seed_dict(tmp_path, "b-dict", "B", styles_css="span { color: blue }")
        css = collect_dictionary_css(
            _config(
                tmp_path,
                ChainEntry(kind="indexed", dict_id="a-dict", enabled=True),
                ChainEntry(kind="indexed", dict_id="b-dict", enabled=False),
            )
        )
        assert '[data-dictionary="A"]' in css
        assert '[data-dictionary="B"]' not in css

    def test_skips_dict_without_styles(self, tmp_path: Path):
        _seed_dict(tmp_path, "a-dict", "A", styles_css=None)
        assert (
            collect_dictionary_css(_config(tmp_path, ChainEntry(kind="indexed", dict_id="a-dict", enabled=True))) == ""
        )

    def test_skips_jisho_online_provider(self, tmp_path: Path):
        _seed_dict(tmp_path, "a-dict", "A", styles_css="span { color: red }")
        css = collect_dictionary_css(
            _config(
                tmp_path,
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
                ChainEntry(kind="indexed", dict_id="a-dict", enabled=True),
            )
        )
        assert '[data-dictionary="A"]' in css
        # No crash from the online provider; it simply contributes nothing.

    def test_distinct_titles_stay_isolated(self, tmp_path: Path):
        _seed_dict(tmp_path, "a-dict", "A", styles_css="span { color: red }")
        _seed_dict(tmp_path, "b-dict", "B", styles_css="span { color: blue }")
        css = collect_dictionary_css(
            _config(
                tmp_path,
                ChainEntry(kind="indexed", dict_id="a-dict", enabled=True),
                ChainEntry(kind="indexed", dict_id="b-dict", enabled=True),
            )
        )
        # Each dict's rule is prefixed with ITS OWN [data-dictionary] scope, so a
        # rule can't leak across distinct-title dicts in the concatenated sheet.
        assert '[data-dictionary="A"] span {color: red}' in css
        assert '[data-dictionary="B"] span {color: blue}' in css


def make_provider(name="Test", available=True, return_value=None, load_raises=None):
    """Create a mock DictionaryProvider with configurable behavior.

    Specced to the per-word Protocol surface only (no ``lookup_many``) so the
    batch fast-path treats these as legacy/online providers and falls back to
    per-word ``lookup`` — matching the assertions in these tests.
    """
    p = MagicMock(spec=["name", "is_online", "is_available", "lookup", "load", "close"])
    p.name = name
    p.is_online = False  # default; tests override as needed
    p.is_available.return_value = available
    p.lookup.return_value = return_value
    if load_raises is not None:
        p.load.side_effect = load_raises
    else:
        p.load.return_value = True
    return p


class TestGetDefinition:
    """Single-word chain walking via get_definitions_batch([word])[0].

    The per-word fallback inside get_definitions_batch (providers lacking
    lookup_many) walks the chain identically to the old get_definition.
    """

    def test_first_hit_wins(self, test_config):
        """When the first provider returns a definition, later providers are not called."""
        p1 = make_provider("A", return_value="from A")
        p2 = make_provider("B", return_value="from B")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definitions_batch([("x", None)])[0] == "from A"
        p1.lookup.assert_called_once_with("x")
        p2.lookup.assert_not_called()

    def test_falls_through_when_first_misses(self, test_config):
        """When the first provider returns None, fall through to the next provider."""
        p1 = make_provider("A", return_value=None)
        p2 = make_provider("B", return_value="from B")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definitions_batch([("x", None)])[0] == "from B"
        p1.lookup.assert_called_once_with("x")
        p2.lookup.assert_called_once_with("x")

    def test_skips_unavailable_provider(self, test_config):
        """Providers where is_available() returns False are skipped without calling lookup()."""
        p1 = make_provider("offline", available=False, return_value="should not be returned")
        p2 = make_provider("online", available=True, return_value="online result")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definitions_batch([("x", None)])[0] == "online result"
        p1.lookup.assert_not_called()
        p2.lookup.assert_called_once()

    def test_returns_none_when_all_miss(self, test_config):
        """When every provider returns None, the result is None."""
        p1 = make_provider("A", return_value=None)
        p2 = make_provider("B", return_value=None)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definitions_batch([("unknown", None)])[0] is None

    def test_returns_none_when_no_providers(self, test_config):
        """Empty provider list yields None for every lookup."""
        service = DefinitionService(test_config, providers=[])
        assert service.get_definitions_batch([("x", None)])[0] is None

    def test_returns_none_when_all_unavailable(self, test_config):
        """When every provider is unavailable, the result is None."""
        p1 = make_provider("A", available=False)
        p2 = make_provider("B", available=False)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definitions_batch([("x", None)])[0] is None
        p1.lookup.assert_not_called()
        p2.lookup.assert_not_called()


class TestEnsureLoaded:
    """Tests for DefinitionService.ensure_loaded idempotence and load() dispatch."""

    def test_calls_load_on_every_provider(self, test_config):
        """ensure_loaded must invoke load() on each provider exactly once."""
        p1 = make_provider("A")
        p2 = make_provider("B")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.ensure_loaded()

        p1.load.assert_called_once()
        p2.load.assert_called_once()

    def test_returns_true_when_at_least_one_available(self, test_config):
        """Returns True when any provider is_available() after load."""
        p1 = make_provider("A", available=False)
        p2 = make_provider("B", available=True)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.ensure_loaded() is True

    def test_returns_false_when_no_provider_available(self, test_config):
        """Returns False when every provider is_available() is False."""
        p1 = make_provider("A", available=False)
        p2 = make_provider("B", available=False)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.ensure_loaded() is False

    def test_returns_false_when_no_providers_configured(self, test_config):
        """Returns False when the provider list is empty."""
        service = DefinitionService(test_config, providers=[])
        assert service.ensure_loaded() is False

    def test_idempotent_load(self, test_config):
        """Multiple calls to ensure_loaded() only invoke provider.load() once."""
        p1 = make_provider("A")
        service = DefinitionService(test_config, providers=[p1])

        service.ensure_loaded()
        service.ensure_loaded()
        service.ensure_loaded()

        p1.load.assert_called_once()

    def test_swallows_provider_load_exception(self, test_config):
        """A provider raising during load() must not abort the chain."""
        p1 = make_provider("Broken", available=False, load_raises=Exception("boom"))
        p2 = make_provider("Working", available=True, return_value="ok")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.ensure_loaded() is True
        assert service.get_definitions_batch([("x", None)])[0] == "ok"

    def test_batch_lookup_triggers_ensure_loaded(self, test_config):
        """Calling get_definitions_batch() lazily loads providers."""
        p1 = make_provider("A", return_value="hit")
        service = DefinitionService(test_config, providers=[p1])

        # Did not call ensure_loaded explicitly
        result = service.get_definitions_batch([("x", None)])[0]

        p1.load.assert_called_once()
        assert result == "hit"


class TestGetDefinitionsBatch:
    """Tests for DefinitionService.get_definitions_batch."""

    def test_returns_definitions_in_order(self, test_config):
        """Returned list mirrors the input word order."""
        responses = {"a": "def-a", "b": None, "c": "def-c"}
        p = make_provider("M", available=True)
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        results = service.get_definitions_batch([("a", None), ("b", None), ("c", None)])

        assert results == ["def-a", None, "def-c"]

    def test_empty_list_returns_empty_list(self, test_config):
        """Empty input yields an empty result list."""
        service = DefinitionService(test_config, providers=[])
        assert service.get_definitions_batch([]) == []

    def test_progress_callback_called_correctly(self, test_config, recording_progress):
        """Progress callbacks fire with the expected counts and statuses."""
        responses = {"a": "def-a", "b": None}
        p = make_provider("M", available=True)
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        service.get_definitions_batch([("a", None), ("b", None)], progress_callback=recording_progress)

        # on_start called once with total count and description
        assert len(recording_progress.starts) == 1
        assert recording_progress.starts[0] == (2, "Fetching definitions")

        # on_progress called for each word (1-indexed)
        assert len(recording_progress.progresses) == 2
        assert recording_progress.progresses[0] == (1, "Definition found: a")
        assert recording_progress.progresses[1] == (2, "No definition: b")

        # on_complete called once
        assert recording_progress.completes == 1

    def test_batch_walks_chain_per_word(self, test_config):
        """Each word triggers the full chain walk independently."""
        p1 = make_provider("first", available=True)
        p1.lookup.side_effect = lambda w: "first-only" if w == "a" else None
        p2 = make_provider("second", available=True, return_value="second-result")
        service = DefinitionService(test_config, providers=[p1, p2])

        results = service.get_definitions_batch([("a", None), ("b", None)])

        assert results == ["first-only", "second-result"]


def make_batch_provider(name="Batch", available=True, table=None):
    """Mock provider supporting lookup_many. ``table`` maps word -> html|None."""
    table = table or {}
    p = MagicMock()
    p.name = name
    p.is_online = False
    p.is_available.return_value = available
    p.load.return_value = True
    p.lookup.side_effect = lambda w: table.get(w)
    p.lookup_many.side_effect = lambda pairs: {w: table.get(w) for w, _ in pairs}
    return p


class TestGetDefinitionsBatchFastPath:
    """Batch fast-path via lookup_many — preserves first-hit-wins semantics."""

    def test_first_hit_wins_skips_second_provider_for_resolved_word(self, test_config):
        p1 = make_batch_provider("A", table={"x": "from A"})
        p2 = make_batch_provider("B", table={"x": "from B", "y": "from B"})
        service = DefinitionService(test_config, providers=[p1, p2])

        results = service.get_definitions_batch([("x", None), ("y", None)])

        assert results == ["from A", "from B"]
        # p2.lookup_many must be called only for the still-unfilled word(s), not "x"
        p2.lookup_many.assert_called_once()
        called_words = [w for w, _ in p2.lookup_many.call_args[0][0]]
        assert "x" not in called_words
        assert "y" in called_words

    def test_batch_matches_expected_chain_resolution(self, test_config):
        p1 = make_batch_provider("A", table={"a": "A-a", "c": "A-c"})
        p2 = make_batch_provider("B", table={"b": "B-b", "c": "B-c-shadowed"})
        words = [("a", None), ("b", None), ("c", None), ("d", None)]
        service = DefinitionService(test_config, providers=[p1, p2])

        batch = service.get_definitions_batch(words)
        # First-hit-wins across the chain: p1 shadows p2 for "c", "d" misses both.
        assert batch == ["A-a", "B-b", "A-c", None]

    def test_word_absent_from_all_providers_is_none(self, test_config):
        p1 = make_batch_provider("A", table={})
        p2 = make_batch_provider("B", table={})
        service = DefinitionService(test_config, providers=[p1, p2])
        assert service.get_definitions_batch([("nope", None)]) == [None]

    def test_falls_back_to_per_word_for_provider_without_lookup_many(self, test_config):
        # provider without lookup_many (Jisho-like)
        legacy = MagicMock(spec=["name", "is_online", "is_available", "lookup", "load"])
        legacy.name = "Legacy"
        legacy.is_online = False
        legacy.is_available.return_value = True
        legacy.load.return_value = True
        legacy.lookup.side_effect = lambda w: {"y": "legacy-y"}.get(w)

        p1 = make_batch_provider("A", table={"x": "A-x"})
        service = DefinitionService(test_config, providers=[p1, legacy])

        results = service.get_definitions_batch([("x", None), ("y", None)])
        assert results == ["A-x", "legacy-y"]
        # legacy queried per-word only for the unfilled "y", never "x"
        legacy.lookup.assert_called_once_with("y")

    def test_unavailable_batch_provider_skipped(self, test_config):
        p1 = make_batch_provider("A", available=False, table={"x": "A-x"})
        p2 = make_batch_provider("B", table={"x": "B-x"})
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definitions_batch([("x", None)]) == ["B-x"]
        p1.lookup_many.assert_not_called()

    def test_preserves_order_and_progress(self, test_config, recording_progress):
        p = make_batch_provider("M", table={"a": "def-a", "b": None})
        service = DefinitionService(test_config, providers=[p])

        results = service.get_definitions_batch([("a", None), ("b", None)], progress_callback=recording_progress)
        assert results == ["def-a", None]
        assert recording_progress.starts[0] == (2, "Fetching definitions")
        assert recording_progress.completes == 1


class TestConfigStored:
    """The config object is stored verbatim (no mutation)."""

    def test_config_is_stored(self):
        """The passed config is accessible on the service."""
        config = AnkiMinerConfig()
        service = DefinitionService(config, providers=[])
        assert service.config is config


class TestGetGlossary:
    """Tests for DefinitionService.get_glossary — collect-all with online fallback."""

    def test_concatenates_all_offline_hits(self, test_config):
        p1 = make_provider("A", return_value="<div>A</div>")
        p1.is_online = False
        p2 = make_provider("B", return_value="<div>B</div>")
        p2.is_online = False
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_glossary("x") == "<div>A</div><div>B</div>"
        p1.lookup.assert_called_once_with("x")
        p2.lookup.assert_called_once_with("x")

    def test_skips_online_when_offline_hit_exists(self, test_config):
        offline = make_provider("Off", return_value="<div>off</div>")
        offline.is_online = False
        online = make_provider("Jisho", return_value="<div>online</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        assert service.get_glossary("x") == "<div>off</div>"
        offline.lookup.assert_called_once_with("x")
        online.lookup.assert_not_called()

    def test_uses_online_when_no_offline_hits(self, test_config):
        offline = make_provider("Off", return_value=None)
        offline.is_online = False
        online = make_provider("Jisho", return_value="<div>online</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        assert service.get_glossary("x") == "<div>online</div>"
        offline.lookup.assert_called_once_with("x")
        online.lookup.assert_called_once_with("x")

    def test_returns_none_when_all_miss(self, test_config):
        offline = make_provider("Off", return_value=None)
        offline.is_online = False
        online = make_provider("Jisho", return_value=None)
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        assert service.get_glossary("x") is None

    def test_skips_unavailable_providers(self, test_config):
        unavail = make_provider("X", available=False, return_value="<div>X</div>")
        unavail.is_online = False
        ok = make_provider("Y", available=True, return_value="<div>Y</div>")
        ok.is_online = False
        service = DefinitionService(test_config, providers=[unavail, ok])

        assert service.get_glossary("x") == "<div>Y</div>"
        unavail.lookup.assert_not_called()


def make_batch_offline_provider(name="BatchOff", available=True, table=None):
    """Mock offline provider supporting lookup_many (for OVH-050 tests)."""
    table = table or {}
    p = MagicMock()
    p.name = name
    p.is_online = False
    p.is_available.return_value = available
    p.load.return_value = True
    p.lookup.side_effect = lambda w: table.get(w)
    p.lookup_many.side_effect = lambda pairs: {w: table.get(w) for w, _ in pairs}
    return p


class TestGetGlossariesBatch:
    """Tests for DefinitionService.get_glossaries_batch."""

    def test_returns_glossaries_in_order(self, test_config):
        responses = {"a": "<div>a</div>", "b": None, "c": "<div>c</div>"}
        p = make_provider("M", available=True)
        p.is_online = False
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        results = service.get_glossaries_batch([("a", None), ("b", None), ("c", None)])

        assert results == ["<div>a</div>", None, "<div>c</div>"]

    def test_empty_list_returns_empty_list(self, test_config):
        service = DefinitionService(test_config, providers=[])
        assert service.get_glossaries_batch([]) == []

    def test_progress_callback_fires(self, test_config, recording_progress):
        responses = {"a": "<div>a</div>", "b": None}
        p = make_provider("M", available=True)
        p.is_online = False
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        service.get_glossaries_batch([("a", None), ("b", None)], progress_callback=recording_progress)

        assert recording_progress.starts[0] == (2, "Fetching glossary entries")
        assert recording_progress.progresses[0] == (1, "Glossary found: a")
        assert recording_progress.progresses[1] == (2, "No glossary: b")
        assert recording_progress.completes == 1


# ---------------------------------------------------------------------------
# OVH-050: get_glossaries_batch batch fast-path
# ---------------------------------------------------------------------------


class TestGetGlossariesBatchFastPath:
    """get_glossaries_batch must use lookup_many for offline providers that expose it,
    and output must be byte-identical to the per-word baseline."""

    def test_lookup_many_called_instead_of_per_word_lookup(self, test_config):
        """For an offline provider with lookup_many, per-word lookup must NOT be called."""
        p = make_batch_offline_provider("Off", table={"x": "<div>x</div>", "y": "<div>y</div>"})
        service = DefinitionService(test_config, providers=[p])

        results = service.get_glossaries_batch([("x", None), ("y", None)])

        # batch path was used
        p.lookup_many.assert_called_once()
        # per-word lookup must not have been called for these words
        p.lookup.assert_not_called()
        assert results == ["<div>x</div>", "<div>y</div>"]

    def test_output_byte_identical_to_per_word_path(self, test_config):
        """Batch output == per-word-loop output."""
        table = {"a": "<div>A</div>", "b": "<div>B</div>", "c": None}
        p = make_batch_offline_provider("Off", table=table)
        service = DefinitionService(test_config, providers=[p])

        batch_results = service.get_glossaries_batch([("a", None), ("b", None), ("c", None)])

        # Build per-word baseline directly
        per_word = [p.lookup(w) for w in ["a", "b", "c"]]
        assert batch_results == per_word

    def test_two_offline_providers_both_use_batch(self, test_config):
        """Both offline providers with lookup_many are batched; per-word lookup not called."""
        p1 = make_batch_offline_provider("Off1", table={"x": "<div>X1</div>"})
        p2 = make_batch_offline_provider("Off2", table={"x": "<div>X2</div>", "y": "<div>Y2</div>"})
        service = DefinitionService(test_config, providers=[p1, p2])

        results = service.get_glossaries_batch([("x", None), ("y", None)])

        p1.lookup.assert_not_called()
        p2.lookup.assert_not_called()
        # x hits both providers → concatenated; y hits only p2
        assert results == ["<div>X1</div><div>X2</div>", "<div>Y2</div>"]

    def test_online_provider_still_falls_back_per_word(self, test_config):
        """Online provider (no lookup_many on chain) remains per-word for misses."""
        offline = make_batch_offline_provider("Off", table={"x": "<div>X</div>"})
        online = make_provider("Jisho", return_value="<div>J</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        results = service.get_glossaries_batch([("x", None), ("z", None)])

        # x has offline hit → online not consulted for x
        online.lookup.assert_called_once_with("z")
        assert results == ["<div>X</div>", "<div>J</div>"]

    def test_missing_words_are_none(self, test_config):
        """Words with no provider hits produce None."""
        p = make_batch_offline_provider("Off", table={})
        service = DefinitionService(test_config, providers=[p])

        assert service.get_glossaries_batch([("missing", None)]) == [None]

    def test_unavailable_batch_provider_skipped(self, test_config):
        """Unavailable providers are skipped even if they have lookup_many."""
        p = make_batch_offline_provider("Off", available=False, table={"x": "<div>X</div>"})
        service = DefinitionService(test_config, providers=[p])

        results = service.get_glossaries_batch([("x", None)])
        assert results == [None]
        p.lookup_many.assert_not_called()

    def test_provider_without_lookup_many_falls_back_to_per_word(self, test_config):
        """Legacy offline providers lacking lookup_many still use per-word lookup."""
        legacy = make_provider("Legacy", return_value="<div>L</div>")
        legacy.is_online = False
        service = DefinitionService(test_config, providers=[legacy])

        results = service.get_glossaries_batch([("x", None)])
        legacy.lookup.assert_called_once_with("x")
        assert results == ["<div>L</div>"]


class TestClose:
    """Tests for DefinitionService.close (Issue #30 — Win11 sqlite handle release)."""

    def test_calls_close_on_each_provider_that_has_it(self, test_config):
        """Every provider exposing a ``close`` method must have it invoked."""
        p1 = make_provider("A")
        p2 = make_provider("B")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.close()

        p1.close.assert_called_once()
        p2.close.assert_called_once()

    def test_skips_providers_without_close(self, test_config):
        """Providers without a ``close`` attribute must not raise (Jisho case)."""
        p1 = MagicMock(spec=["name", "is_online", "is_available", "lookup", "load"])
        p1.name = "Jisho"
        p2 = make_provider("Indexed")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.close()  # must not raise even though p1 has no close()
        p2.close.assert_called_once()

    def test_swallows_provider_close_exception(self, test_config):
        """A provider raising during close() must not abort the rest of the chain."""
        p1 = make_provider("Broken")
        p1.close.side_effect = Exception("boom")
        p2 = make_provider("Working")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.close()  # must not raise

        p1.close.assert_called_once()
        p2.close.assert_called_once()

    def test_resets_loaded_so_next_lookup_reopens(self, test_config):
        """After close(), the next batch lookup must re-invoke provider.load()."""
        p1 = make_provider("A", return_value="hit")
        service = DefinitionService(test_config, providers=[p1])

        service.ensure_loaded()
        p1.load.assert_called_once()

        service.close()
        service.get_definitions_batch([("x", None)])

        assert p1.load.call_count == 2

    def test_close_is_idempotent(self, test_config):
        """Two successive close() calls must not raise.

        Required so the tab-level ``release_dictionary_resources`` can be
        invoked repeatedly (e.g. user opens Settings, cancels, reopens)
        without surprises — Issue #30 follow-up that hardens the release
        path used by SingleEpisodeTab and BatchProcessingTab.
        """
        p1 = make_provider("A")
        service = DefinitionService(test_config, providers=[p1])

        service.close()
        service.close()  # must not raise

        assert p1.close.call_count == 2


class TestLookupAllOffline:
    """Tests for DefinitionService.lookup_all_offline — aggregate offline dicts."""

    def test_returns_labeled_tuples_for_available_offline_hits(self, test_config):
        """Offline providers that return hits are included as (name, html)."""
        p1 = make_provider("Dict A", return_value="<div>A</div>")
        p1.is_online = False
        p2 = make_provider("Dict B", return_value="<div>B</div>")
        p2.is_online = False
        service = DefinitionService(test_config, providers=[p1, p2])

        result = service.lookup_all_offline("word")

        assert result == [("Dict A", "<div>A</div>"), ("Dict B", "<div>B</div>")]

    def test_preserves_chain_order(self, test_config):
        """Order of results matches the provider list order."""
        p1 = make_provider("First", return_value="html1")
        p1.is_online = False
        p2 = make_provider("Second", return_value="html2")
        p2.is_online = False
        p3 = make_provider("Third", return_value="html3")
        p3.is_online = False
        service = DefinitionService(test_config, providers=[p1, p2, p3])

        result = service.lookup_all_offline("x")

        names = [name for name, _ in result]
        assert names == ["First", "Second", "Third"]

    def test_excludes_online_provider_even_with_hit(self, test_config):
        """Online providers are skipped even if their lookup returns a hit."""
        offline = make_provider("Off", return_value="<div>offline</div>")
        offline.is_online = False
        online = make_provider("Jisho", return_value="<div>online</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        result = service.lookup_all_offline("x")

        assert result == [("Off", "<div>offline</div>")]
        offline.lookup.assert_called_once_with("x")
        online.lookup.assert_not_called()

    def test_skips_unavailable_offline_provider(self, test_config):
        """Offline providers where is_available() is False are skipped."""
        unavail = make_provider("Bad", available=False, return_value="<div>x</div>")
        unavail.is_online = False
        ok = make_provider("Good", available=True, return_value="<div>y</div>")
        ok.is_online = False
        service = DefinitionService(test_config, providers=[unavail, ok])

        result = service.lookup_all_offline("word")

        assert result == [("Good", "<div>y</div>")]
        unavail.lookup.assert_not_called()

    def test_skips_offline_providers_returning_none(self, test_config):
        """Offline providers that return None are excluded."""
        miss = make_provider("Empty", return_value=None)
        miss.is_online = False
        hit = make_provider("Full", return_value="<div>found</div>")
        hit.is_online = False
        service = DefinitionService(test_config, providers=[miss, hit])

        result = service.lookup_all_offline("x")

        assert result == [("Full", "<div>found</div>")]
        miss.lookup.assert_called_once_with("x")
        hit.lookup.assert_called_once_with("x")

    def test_returns_empty_list_when_nothing_matches(self, test_config):
        """Empty result list when all providers miss or are online."""
        p1 = make_provider("Empty", return_value=None)
        p1.is_online = False
        p2 = make_provider("Online", return_value="<div>o</div>")
        p2.is_online = True
        service = DefinitionService(test_config, providers=[p1, p2])

        result = service.lookup_all_offline("x")

        assert result == []

    def test_returns_empty_list_when_no_providers(self, test_config):
        """Empty provider list returns empty list."""
        service = DefinitionService(test_config, providers=[])

        result = service.lookup_all_offline("x")

        assert result == []

    def test_calls_ensure_loaded(self, test_config):
        """lookup_all_offline triggers ensure_loaded() before lookups."""
        p1 = make_provider("A", return_value="hit")
        p1.is_online = False
        service = DefinitionService(test_config, providers=[p1])

        service.lookup_all_offline("x")

        p1.load.assert_called_once()

    def test_mixed_online_offline_with_multiple_hits(self, test_config):
        """Integration: multiple offline, one online; excludes online."""
        off1 = make_provider("Off1", return_value="<div>1</div>")
        off1.is_online = False
        off2 = make_provider("Off2", return_value="<div>2</div>")
        off2.is_online = False
        online = make_provider("Jisho", return_value="<div>j</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[off1, online, off2])

        result = service.lookup_all_offline("word")

        assert result == [("Off1", "<div>1</div>"), ("Off2", "<div>2</div>")]
        off1.lookup.assert_called_once_with("word")
        online.lookup.assert_not_called()
        off2.lookup.assert_called_once_with("word")


class TestProviderRaisesMidChain:
    """A provider raising DURING a lookup is skipped (degrade-and-warn, OVH-046).

    ``ensure_loaded`` (which wraps ``provider.load`` in try/except) and
    ``close`` (which wraps ``provider.close``) already guard per-provider calls.
    The per-word ``lookup`` / batch ``lookup_many`` calls in
    ``get_definitions_batch``, ``get_glossary``, ``get_glossaries_batch``, and
    ``lookup_all_offline`` now match that pattern: a raising provider is logged,
    treated as a miss, and the chain continues — earlier hits are preserved.
    """

    def test_get_definitions_batch_per_word_skip_and_continue(self, test_config):
        p = make_provider("Boom")
        p.lookup.side_effect = RuntimeError("provider boom")
        service = DefinitionService(test_config, providers=[p])

        result = service.get_definitions_batch([("x", None)])
        assert result == [None]

    def test_get_definitions_batch_lookup_many_skip_and_continue(self, test_config):
        p = make_batch_provider("BatchBoom")
        p.lookup_many.side_effect = RuntimeError("batch boom")
        service = DefinitionService(test_config, providers=[p])

        result = service.get_definitions_batch([("x", None)])
        assert result == [None]

    def test_earlier_provider_hits_survive_when_later_provider_raises(self, test_config):
        """Earlier hit is preserved when a later provider raises."""
        p_ok = make_provider("OK")
        p_ok.lookup.side_effect = lambda w: "hit-a" if w == "a" else None
        p_boom = make_provider("Boom")
        p_boom.lookup.side_effect = RuntimeError("second boom")
        service = DefinitionService(test_config, providers=[p_ok, p_boom])

        # "a" resolves on p_ok; "b" falls through to p_boom which raises — skipped.
        result = service.get_definitions_batch([("a", None), ("b", None)])
        assert result == ["hit-a", None]

    def test_get_glossary_offline_skip_and_continue(self, test_config):
        p = make_provider("Boom", return_value=None)
        p.is_online = False
        p.lookup.side_effect = RuntimeError("glossary boom")
        service = DefinitionService(test_config, providers=[p])

        result = service.get_glossary("x")
        assert result is None

    def test_get_glossary_online_skip_after_offline_miss(self, test_config):
        """The online fallback raising is also skipped (offline missed first)."""
        offline = make_provider("Off", return_value=None)
        offline.is_online = False
        online = make_provider("Jisho")
        online.is_online = True
        online.lookup.side_effect = RuntimeError("online boom")
        service = DefinitionService(test_config, providers=[offline, online])

        result = service.get_glossary("x")
        assert result is None

    def test_get_glossaries_batch_skip_and_continue(self, test_config):
        p = make_provider("Boom", return_value=None)
        p.is_online = False
        p.lookup.side_effect = RuntimeError("glossaries boom")
        service = DefinitionService(test_config, providers=[p])

        result = service.get_glossaries_batch([("x", None)])
        assert result == [None]

    def test_lookup_all_offline_skip_and_continue(self, test_config):
        p = make_provider("Boom")
        p.is_online = False
        p.lookup.side_effect = RuntimeError("offline boom")
        service = DefinitionService(test_config, providers=[p])

        result = service.lookup_all_offline("x")
        assert result == []

    def test_raising_provider_warned_in_log(self, test_config, caplog):
        """A raising provider emits a warning log."""
        import logging

        p = make_provider("BadProv")
        p.lookup.side_effect = RuntimeError("kaboom")
        service = DefinitionService(test_config, providers=[p])

        caplog.set_level(logging.WARNING)
        service.get_definitions_batch([("w", None)])
        assert "BadProv" in caplog.text


class TestHasOfflineDefinitions:
    """Offline-only existence probe used to pre-filter the curation dialog."""

    def test_reports_true_only_for_offline_hits(self, test_config):
        p = make_batch_provider("Off", table={"a": "<div>a</div>", "b": None})
        service = DefinitionService(test_config, providers=[p])

        result = service.has_offline_definitions(["a", "b"])

        assert result == {"a": True, "b": False}

    def test_per_word_fallback_provider(self, test_config):
        """Providers lacking lookup_many are consulted per-word."""
        p = make_provider("Legacy", return_value=None)
        p.is_online = False
        p.lookup.side_effect = lambda w: "<div>hit</div>" if w == "x" else None
        service = DefinitionService(test_config, providers=[p])

        result = service.has_offline_definitions(["x", "y"])

        assert result == {"x": True, "y": False}

    def test_online_provider_ignored_even_with_hit(self, test_config):
        """Online providers never contribute and never get queried."""
        online = make_provider("Jisho", return_value="<div>online</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[online])

        result = service.has_offline_definitions(["x"])

        assert result == {"x": False}
        online.lookup.assert_not_called()

    def test_offline_hit_short_circuits_remaining_providers(self, test_config):
        """A word resolved offline is not re-queried against later providers."""
        p1 = make_batch_provider("First", table={"x": "<div>x</div>"})
        p2 = make_batch_provider("Second", table={"x": "<div>x2</div>"})
        service = DefinitionService(test_config, providers=[p1, p2])

        result = service.has_offline_definitions(["x"])

        assert result == {"x": True}
        # Second provider only sees words still unresolved after the first.
        assert p2.lookup_many.call_args is None or "x" not in [w for w, _ in p2.lookup_many.call_args[0][0]]

    def test_skips_unavailable_provider(self, test_config):
        unavail = make_batch_provider("Bad", available=False, table={"x": "<div>x</div>"})
        ok = make_batch_provider("Good", table={"x": "<div>x</div>"})
        service = DefinitionService(test_config, providers=[unavail, ok])

        result = service.has_offline_definitions(["x"])

        assert result == {"x": True}
        unavail.lookup_many.assert_not_called()

    def test_provider_exception_degrades_to_miss(self, test_config):
        """A raising provider is treated as a miss, never aborting the probe."""
        boom = make_provider("Boom")
        boom.is_online = False
        boom.lookup.side_effect = RuntimeError("offline boom")
        service = DefinitionService(test_config, providers=[boom])

        result = service.has_offline_definitions(["x"])

        assert result == {"x": False}

    def test_dedupes_keys(self, test_config):
        p = make_batch_provider("Off", table={"a": "<div>a</div>"})
        service = DefinitionService(test_config, providers=[p])

        result = service.has_offline_definitions(["a", "a", "a"])

        assert result == {"a": True}

    def test_empty_words(self, test_config):
        p = make_batch_provider("Off", table={})
        service = DefinitionService(test_config, providers=[p])

        assert service.has_offline_definitions([]) == {}


def make_has_terms_provider(name="HT", table=None, available=True, online=False):
    """Mock offline provider exposing ``has_terms`` (compound matching)."""
    table = table or set()
    p = MagicMock(spec=["name", "is_online", "is_available", "lookup", "load", "close", "has_terms"])
    p.name = name
    p.is_online = online
    p.is_available.return_value = available
    p.load.return_value = True
    p.has_terms.side_effect = lambda terms: table & set(terms)
    return p


class TestOfflineTermsExist:
    """offline_terms_exist — exact-headword union across offline has_terms providers."""

    def test_union_across_two_providers_with_early_exit(self, test_config):
        p1 = make_has_terms_provider("A", {"走り出す"})
        p2 = make_has_terms_provider("B", {"応急処置"})
        service = DefinitionService(test_config, providers=[p1, p2])

        found = service.offline_terms_exist(["走り出す", "応急処置", "無い語"])

        assert found == {"走り出す", "応急処置"}
        # early-exit: p2 must only be asked about terms p1 did not attest
        p2.has_terms.assert_called_once()
        assert "走り出す" not in p2.has_terms.call_args[0][0]

    def test_online_provider_skipped(self, test_config):
        online = make_has_terms_provider("Jisho", {"走り出す"}, online=True)
        service = DefinitionService(test_config, providers=[online])
        assert service.offline_terms_exist(["走り出す"]) == set()
        online.has_terms.assert_not_called()

    def test_unavailable_provider_skipped(self, test_config):
        p = make_has_terms_provider("A", {"走り出す"}, available=False)
        service = DefinitionService(test_config, providers=[p])
        assert service.offline_terms_exist(["走り出す"]) == set()
        p.has_terms.assert_not_called()

    def test_provider_without_has_terms_attests_nothing(self, test_config):
        legacy = make_provider("Legacy", return_value="<div>hit</div>")
        service = DefinitionService(test_config, providers=[legacy])
        assert service.offline_terms_exist(["走り出す"]) == set()
        legacy.lookup.assert_not_called()  # no per-word fallback by design

    def test_raising_provider_skipped_others_consulted(self, test_config):
        bad = make_has_terms_provider("Bad")
        bad.has_terms.side_effect = RuntimeError("boom")
        good = make_has_terms_provider("Good", {"気がする"})
        service = DefinitionService(test_config, providers=[bad, good])
        assert service.offline_terms_exist(["気がする"]) == {"気がする"}

    def test_no_providers(self, test_config):
        service = DefinitionService(test_config, providers=[])
        assert service.offline_terms_exist(["走り出す"]) == set()

    def test_duplicates_collapsed(self, test_config):
        p = make_has_terms_provider("A", {"走り出す"})
        service = DefinitionService(test_config, providers=[p])
        assert service.offline_terms_exist(["走り出す", "走り出す"]) == {"走り出す"}
        assert p.has_terms.call_args[0][0] == ["走り出す"]
