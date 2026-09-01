"""The ko stack reports why it is unavailable, and gates the selector and switch.

The Korean profile builds on a machine with no ``kiwipiepy`` at all - every
third-party import in ``languages/ko`` is function-local - so building it proves
nothing about whether the language can mine a word. ``unavailable_reason`` is the
runtime probe that decides: without it the selector offers ko and the switch
proceeds on an install missing the engine, and mining then dies with "No
tokenizer registered" long after the user made the choice.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers import language_switch
from anki_miner.gui.utils import language_choices
from anki_miner.languages.ko import availability
from anki_miner.languages.registry import get_profile

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class TestMissingRequiredReason:
    def test_a_complete_stack_reports_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "_installed", lambda _name: True)
        assert availability.ko_missing_required_reason() is None

    def test_a_missing_package_is_named_with_the_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "_installed", lambda name: name != "kiwipiepy")
        reason = availability.ko_missing_required_reason()
        assert reason is not None
        assert "kiwipiepy" in reason
        assert "anki-miner[ko]" in reason

    def test_every_missing_package_is_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "_installed", lambda _name: False)
        reason = availability.ko_missing_required_reason() or ""
        for package in availability.KO_REQUIRED_PACKAGES:
            assert package in reason

    def test_the_model_alone_gates_the_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both packages are hard requirements: Kiwi() raises without the model."""
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        reason = availability.ko_missing_required_reason() or ""
        assert "kiwipiepy_model" in reason
        assert reason.count("kiwipiepy") == 1  # the engine itself is not named

    def test_the_probe_reads_the_real_environment(self) -> None:
        # _installed answers from find_spec, so an installed stdlib module is a
        # true probe and a nonsense name is a false one - no import executed.
        assert availability._installed("json") is True
        assert availability._installed("definitely_not_a_real_package_zzz") is False

    def test_a_frozen_build_names_the_pack_not_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A frozen bundle has no pip: naming the extra would be dead advice, so
        # every missing tier collapses onto the one button-naming sentence.
        monkeypatch.setattr(availability, "_installed", lambda _name: False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        reason = availability.ko_missing_required_reason()

        assert reason == ("Korean mining needs the Korean language pack. Download it in Settings -> Mining Language.")
        assert "pip install" not in (reason or "")

    def test_a_frozen_build_with_only_the_model_missing_still_names_the_pack(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        reason = availability.ko_missing_required_reason()

        assert reason == ("Korean mining needs the Korean language pack. Download it in Settings -> Mining Language.")

    def test_a_pip_build_is_unaffected_by_the_frozen_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(availability, "_installed", lambda _name: False)

        reason = availability.ko_missing_required_reason() or ""

        assert 'pip install "anki-miner[ko]"' in reason

    def test_a_pip_build_also_names_the_download_button(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pack ships the engine AND the model, so a source user with
        neither has two ways out - naming only pip hides the in-app one."""
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(availability, "_installed", lambda _name: False)

        reason = availability.ko_missing_required_reason() or ""

        assert "Settings -> Mining Language" in reason

    def test_the_download_hint_names_the_current_settings_path(self) -> None:
        # No stale "Filtering ->" segment - Mining Language moved out from under it.
        assert "Filtering" not in availability.KO_MODEL_DOWNLOAD_HINT
        assert "Settings -> Mining Language" in availability.KO_MODEL_DOWNLOAD_HINT


def test_the_profile_wires_the_required_only_probe() -> None:
    assert get_profile("ko").unavailable_reason is availability.ko_missing_required_reason


def test_a_missing_engine_drops_ko_from_the_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy" else object())

    assert "ko" not in [code for code, _name in language_choices.available_mining_languages()]


def test_a_present_engine_keeps_ko_offered_under_its_native_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(availability, "find_spec", lambda _name: object())

    assert dict(language_choices.available_mining_languages())["ko"] == "한국어"


class _FakeWindow:
    """The slice of MainWindow a refusal touches - nothing past the probe."""

    def __init__(self, config: AnkiMinerConfig) -> None:
        self.config = config
        self.issues: list[str] = []

    def get_config(self) -> AnkiMinerConfig:
        return self.config

    def show_screen_issue(self, issue, action=None) -> None:
        self.issues.append(issue.summary)

    def _dictionary_mutation_guard(self, kind: str):  # pragma: no cover - never reached
        raise AssertionError("the switch must refuse before the guard")


def test_a_missing_engine_refuses_the_switch_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy" else object())
    window = _FakeWindow(AnkiMinerConfig())

    assert language_switch.request_language_change(window, "ko") is False
    assert window.config.language == "ja"
    assert window.issues and "kiwipiepy" in window.issues[0]


def test_a_present_engine_lets_the_switch_reach_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe is the only gate this test cares about; the guard is the proof."""
    monkeypatch.setattr(availability, "find_spec", lambda _name: object())
    window = _FakeWindow(AnkiMinerConfig())

    with pytest.raises(AssertionError, match="refuse before the guard"):
        language_switch.request_language_change(window, "ko")


def test_ko_modules_never_import_the_extra_at_module_level() -> None:
    # get_profile("ko") must build with no extra installed - that is what lets
    # the selector ask the probe instead of crashing - so every kiwipiepy
    # import has to sit inside a function body.
    package_dir = PROJECT_ROOT / "anki_miner" / "languages" / "ko"
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert not ({"kiwipiepy", "kiwipiepy_model"} & set(names)), f"{path.name} imports an extra eagerly"


def test_ja_carries_no_probe_and_is_always_offered() -> None:
    """Only an optional-extra language gates itself; ja must never disappear."""
    assert get_profile("ja").unavailable_reason is None
