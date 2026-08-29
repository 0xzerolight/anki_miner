"""Phase-5 card render hooks — the non-ja extra-field seam (task 1A.9).

``EpisodeProcessor`` now holds a ``LanguageProfile`` and, for a non-ja run,
merges each hook's LOGICAL ``anki_fields`` keys into phase 5's ``extra_fields``.

Japanese renders its pitch / furigana / frequency / glossary fields inline in
``_phase5_create`` and is gated out of the loop entirely — controller ruling R3
makes the phase-5 language-code check a sanctioned site — so the ja payload is
unreachable by a hook even when one is somehow present on the profile. That
gate is what the JA drift canary depends on.

Non-ja profiles are stubbed with ``dataclasses.replace`` of the ja profile
(controller ruling R6): ``get_profile("zh")`` raises until Stage 2A and no test
may register a builder in the process-wide registry.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from anki_miner.languages.profile import LanguageProfile
from anki_miner.languages.registry import get_profile
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.orchestration.episode_processor import _EpisodeContext
from tests.conftest import build_processor


def _profile_with(*, hooks: tuple[Any, ...] = (), code: str = "zh") -> LanguageProfile:
    """A complete non-ja profile: the ja profile with hooks swapped in."""
    return dataclasses.replace(get_profile("ja"), code=code, render_hooks=hooks)


def _word(mined: str = "電影") -> TokenizedWord:
    return TokenizedWord(
        surface=mined,
        lemma=mined,
        reading="",
        sentence=f"{mined}。",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        pos="名詞",
    )


def _ctx() -> _EpisodeContext:
    return _EpisodeContext(
        start_time=time.time(),
        video_file_str="ep.mkv",
        subtitle_file_str="ep.ass",
        episode_name="ep",
        series_name="series",
        source_label="ep",
    )


def _make_processor(config, *, profile: LanguageProfile | None = None, **services: Any):
    definition_service = services.pop("definition_service", None) or MagicMock()
    definition_service.css_entries.return_value = []
    anki_service = services.pop("anki_service", None) or MagicMock()
    anki_service.create_cards_batch.return_value = []
    kwargs: dict[str, Any] = {
        "definition_service": definition_service,
        "anki_service": anki_service,
        **services,
    }
    if profile is not None:
        kwargs["profile"] = profile
    return build_processor(config, **kwargs)


def _run_phase5(config, *, hooks: tuple[Any, ...] = (), word: TokenizedWord | None = None):
    """Drive ``_phase5_create`` once and return the ``CardPayload`` list."""
    proc = _make_processor(config, profile=_profile_with(hooks=hooks))
    target = word if word is not None else _word()
    proc._phase5_create(_ctx(), [(target, MediaData())], ["to eat"], [None], [(None, None)], None)
    return proc.anki_service.create_cards_batch.call_args.args[0]


class _FieldHook:
    """Minimal CardRenderHook returning one fixed logical field."""

    def __init__(self, fields: dict[str, str]) -> None:
        self._fields = fields
        self.words: list[Any] = []

    def field_names(self) -> tuple[str, ...]:
        return tuple(self._fields)

    def render(self, word: Any, *, config: Any) -> dict[str, str]:
        self.words.append(word)
        return dict(self._fields)


class _RaisingHook:
    def field_names(self) -> tuple[str, ...]:
        return ("pinyin",)

    def render(self, word: Any, *, config: Any) -> dict[str, str]:
        raise RuntimeError("hook exploded")


@pytest.fixture
def config_ja(test_config):
    return dataclasses.replace(test_config, language="ja")


@pytest.fixture
def config_zh(test_config):
    return dataclasses.replace(test_config, language="zh")


# --- the profile the processor holds -------------------------------------


def test_profile_defaults_from_config_language(config_ja):
    proc = _make_processor(config_ja)
    assert proc.profile.code == "ja"
    assert proc.profile is get_profile("ja")


def test_an_explicit_profile_is_used_verbatim(config_zh):
    stub = _profile_with()
    assert _make_processor(config_zh, profile=stub).profile is stub


def test_the_ctor_parameter_is_keyword_only_and_optional(config_ja):
    """Every pre-existing construction site stays valid: no positional shift."""
    proc = _make_processor(config_ja)
    assert proc.profile is get_profile("ja")


def test_the_factory_hands_the_processor_the_config_languages_profile(config_zh, monkeypatch):
    """``create_episode_processor`` resolves the profile at the composition root.

    Stubbed the way task 1A.4 stubs it (controller ruling R6): a
    ``dataclasses.replace`` of the real ja profile behind a
    ``service_factory.get_profile`` patch, plus the ja tagger seeded into the
    provider cache for the zh code, since no zh tokenizer exists until Stage 2A.
    """
    from anki_miner.gui.utils import service_factory
    from anki_miner.languages import registry, tagger_provider
    from anki_miner.presenters import NullPresenter

    stub = _profile_with()
    real_get_profile = service_factory.get_profile
    monkeypatch.setattr(
        service_factory,
        "get_profile",
        lambda code: stub if code == "zh" else real_get_profile(code),
    )
    # config_language degrades an unregistered code to ja before get_profile is
    # reached, so the stub is registered too (dropped again at teardown).
    monkeypatch.setitem(registry._BUILDERS, "zh", lambda: stub)
    monkeypatch.setitem(registry._CACHE, "zh", stub)
    monkeypatch.setitem(tagger_provider._TAGGERS, "zh", tagger_provider.get_tagger("ja"))

    proc = service_factory.create_episode_processor(config_zh, NullPresenter())
    assert proc.profile is stub


# --- the ja gate ---------------------------------------------------------


def test_ja_run_never_calls_a_render_hook(config_ja):
    """ja is gated out even if a hook is somehow present."""
    hook = MagicMock()
    hook.field_names.return_value = ("frequency",)
    proc = _make_processor(config_ja, profile=_profile_with(hooks=(hook,), code="ja"))
    extra: dict[str, str] = {}

    proc._apply_render_hooks(_word(), "to eat", extra)

    hook.render.assert_not_called()
    assert extra == {}


def test_a_ja_phase5_run_writes_no_hook_field(config_ja):
    hook = _FieldHook({"measure_word": "個"})
    proc = _make_processor(config_ja, profile=_profile_with(hooks=(hook,), code="ja"))
    proc._phase5_create(_ctx(), [(_word(), MediaData())], ["to eat"], [None], [(None, None)], None)

    payload = proc.anki_service.create_cards_batch.call_args.args[0][0]
    assert hook.words == []
    assert "measure_word" not in (payload.extra_fields or {})


# --- the non-ja merge ----------------------------------------------------


def test_non_ja_run_merges_hook_fields_under_logical_keys(config_zh):
    class Hook:
        def field_names(self) -> tuple[str, ...]:
            return ("measure_word",)

        def render(self, word: Any, *, config: Any) -> dict[str, str]:
            return {"measure_word": "个"}

    payloads = _run_phase5(config_zh, hooks=(Hook(),))
    assert payloads[0].extra_fields["measure_word"] == "个"


def test_every_hook_on_the_profile_contributes(config_zh):
    first = _FieldHook({"pinyin": "diàn yǐng"})
    second = _FieldHook({"measure_word": "个"})
    payloads = _run_phase5(config_zh, hooks=(first, second))
    assert payloads[0].extra_fields["pinyin"] == "diàn yǐng"
    assert payloads[0].extra_fields["measure_word"] == "个"


def test_the_hook_receives_the_tokenized_word_not_a_word_data(config_zh):
    hook = _FieldHook({"pinyin": "diàn yǐng"})
    word = _word()
    _run_phase5(config_zh, hooks=(hook,), word=word)
    assert hook.words == [word]
    assert isinstance(hook.words[0], TokenizedWord)


def test_an_empty_hook_value_is_not_written(config_zh):
    payloads = _run_phase5(config_zh, hooks=(_FieldHook({"pinyin": ""}),))
    assert "pinyin" not in (payloads[0].extra_fields or {})


def test_hook_fields_do_not_clobber_pipeline_fields(config_zh):
    """A hook may only fill a key phase 5 left unset."""
    payloads = _run_phase5(config_zh, hooks=(_FieldHook({"source": "HOOK"}),))
    assert payloads[0].extra_fields["source"].startswith("ep @ ")


def test_the_first_hook_wins_a_key_a_later_hook_repeats(config_zh):
    payloads = _run_phase5(
        config_zh,
        hooks=(_FieldHook({"pinyin": "first"}), _FieldHook({"pinyin": "second"})),
    )
    assert payloads[0].extra_fields["pinyin"] == "first"


def test_a_raising_hook_is_logged_and_the_run_continues(config_zh, caplog):
    survivor = _FieldHook({"measure_word": "个"})
    with caplog.at_level(logging.WARNING, logger="anki_miner.orchestration.episode_processor"):
        payloads = _run_phase5(config_zh, hooks=(_RaisingHook(), survivor))

    assert payloads[0].extra_fields["measure_word"] == "个"
    assert any("_RaisingHook" in record.getMessage() for record in caplog.records)


def test_phase5_passes_the_card_definition_to_the_helper(config_zh, monkeypatch):
    """The three-argument shape Stage 2A task 2A.10 consumes verbatim."""
    calls: list[tuple[Any, str, dict[str, str]]] = []
    proc = _make_processor(config_zh, profile=_profile_with())
    monkeypatch.setattr(
        proc,
        "_apply_render_hooks",
        lambda word, definition, extra_fields: calls.append((word, definition, extra_fields)),
    )
    word = _word()
    proc._phase5_create(_ctx(), [(word, MediaData())], ["to eat"], [None], [(None, None)], None)

    assert len(calls) == 1
    assert calls[0][0] is word
    assert calls[0][1] == "to eat"
    # The same dict phase 5 already filled, so the merge rule can see its keys.
    assert calls[0][2]["source"].startswith("ep @ ")


def test_a_word_with_no_definition_reaches_no_hook(config_zh):
    hook = _FieldHook({"pinyin": "diàn yǐng"})
    proc = _make_processor(config_zh, profile=_profile_with(hooks=(hook,)))
    proc._phase5_create(_ctx(), [(_word(), MediaData())], [None], [None], [(None, None)], None)

    assert hook.words == []
    assert proc.anki_service.create_cards_batch.call_args.args[0] == []
