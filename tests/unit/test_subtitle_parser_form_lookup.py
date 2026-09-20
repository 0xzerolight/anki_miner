"""R36: ``SubtitleParserService(form_lookup=)`` and the proof that it changes nothing else.

The seam is one constructor argument read at exactly one line -- inside the
``self._token_post_pass is not None`` branch of ``_build_line_state``. So the languages at risk are
NOT ja/ko/zh, which inject no post-pass at all; they are the ones that DO, because the builder in
``service_factory`` is language-blind and their third argument flips from ``None`` to a real
callable. Every one of them must ignore it, and the parametrised case below is what proves that.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import anki_miner.services.subtitle_parser as parser_module
from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.subtitle_parser import SubtitleParserService

#: Languages whose post-pass is EXPECTED to read the third argument. Named, so the sweep below
#: stays a real assertion for every other language rather than a list someone can edit away.
FORM_LOOKUP_READERS = frozenset({"he"})


class _Spy:
    def __init__(self, result=None):
        self.calls: list[list[str]] = []
        self._result = result or {}

    def __call__(self, terms):
        self.calls.append(list(terms))
        return self._result


class _DuckToken:
    """The shape every post-pass reads: a surface and a feature namespace."""

    __slots__ = ("surface", "feature", "morph")

    def __init__(self, surface: str) -> None:
        self.surface = surface
        self.feature = SimpleNamespace(pos1="NOUN", pos2="", lemma=surface, kana="")
        self.morph = ""


def _injected_kwargs(code: str) -> dict[str, object] | None:
    """What ``code``'s own parser factory injects, without building a real parser.

    ``None`` for a language whose factory IS the ja one (it builds the service directly) or whose
    engine cannot be imported in this environment.
    """
    profile = get_profile(code)
    factory = profile.create_parser
    if factory is get_profile("ja").create_parser:
        return None
    config = AnkiMinerConfig() if code == "ja" else switch_language(AnkiMinerConfig(), code)
    captured: dict[str, object] = {}

    def _capture(_config, **kwargs):
        captured.update(kwargs)
        return None

    original = parser_module.SubtitleParserService
    parser_module.SubtitleParserService = _capture  # type: ignore[assignment]
    try:
        factory(config)
    except Exception:  # pragma: no cover - a language whose engine is absent here
        return None
    finally:
        parser_module.SubtitleParserService = original  # type: ignore[assignment]
    return captured


def _post_passes() -> list[tuple[str, object]]:
    """Every registered profile's ``token_post_pass``, taken from its own parser factory."""
    found: list[tuple[str, object]] = []
    for code in AVAILABLE_LANGUAGES:
        kwargs = _injected_kwargs(code)
        if kwargs is None:
            continue
        post_pass = kwargs.get("token_post_pass")
        if post_pass is not None:
            found.append((code, post_pass))
    return found


POST_PASSES = _post_passes()


# --------------------------------------------------------------------------
# The constructor argument
# --------------------------------------------------------------------------


def test_the_parameter_defaults_to_none():
    assert inspect.signature(SubtitleParserService.__init__).parameters["form_lookup"].default is None


def test_a_parser_built_without_it_stores_none(test_config):
    assert SubtitleParserService(test_config)._form_lookup is None


def test_the_post_pass_receives_the_very_object_that_was_injected(test_config):
    seen: list[object] = []

    def post_pass(tokens, attest, forms):
        seen.append(forms)
        return tokens

    spy = _Spy()
    parser = SubtitleParserService(test_config, token_post_pass=post_pass, form_lookup=spy)
    parser._build_line_state("x", 0.0, 1.0)
    assert seen and seen[0] is spy


def test_a_post_pass_on_a_parser_without_the_lookup_receives_none(test_config):
    seen: list[object] = []

    def post_pass(tokens, attest, forms):
        seen.append(forms)
        return tokens

    parser = SubtitleParserService(test_config, token_post_pass=post_pass)
    parser._build_line_state("x", 0.0, 1.0)
    assert seen == [None]


def test_a_parser_with_no_post_pass_never_touches_the_lookup(test_config):
    spy = _Spy()
    parser = SubtitleParserService(test_config, form_lookup=spy)
    parser._build_line_state("x", 0.0, 1.0)
    assert spy.calls == []


# --------------------------------------------------------------------------
# The default-identity proof: every registered post-pass ignores its third argument
# --------------------------------------------------------------------------


def test_the_sweep_actually_found_the_languages_that_wire_a_post_pass():
    """Guards the sweep itself: an empty list would make every case below vacuous."""
    codes = {code for code, _ in POST_PASSES}
    assert len(codes) >= 5, codes


@pytest.mark.parametrize("code,post_pass", POST_PASSES, ids=[code for code, _ in POST_PASSES])
def test_every_registered_post_pass_ignores_the_form_lookup(code, post_pass):
    """The builder is language-blind, so these are the languages whose third argument flips."""
    if code in FORM_LOOKUP_READERS:
        pytest.skip(f"{code} is the seam's reader by design")
    tokens = [_DuckToken("word"), _DuckToken("other")]
    spy = _Spy()

    without = post_pass([_DuckToken(t.surface) for t in tokens], None, None)
    with_lookup = post_pass([_DuckToken(t.surface) for t in tokens], None, spy)

    assert [t.surface for t in with_lookup] == [t.surface for t in without]
    assert spy.calls == [], f"{code}'s post-pass read the form lookup"


# --------------------------------------------------------------------------
# ja/ko/zh keep their identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["ja", "ko", "zh"])
def test_the_three_shipped_languages_inject_no_post_pass(code):
    kwargs = _injected_kwargs(code)
    if kwargs is None:
        assert code == "ja", f"{code}'s factory should have been callable"
        return
    assert kwargs.get("token_post_pass") is None
