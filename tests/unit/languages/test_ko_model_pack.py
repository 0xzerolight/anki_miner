"""The Korean model resolves from the installed package OR the download pack.

The bundle ships the kiwipiepy ENGINE but not its ~88 MB model, so a frozen
install has to find the model in the in-app pack instead. Both halves of the
ladder matter: a dev/CI machine with ``kiwipiepy-model`` on ``sys.path`` must keep
working untouched, and a bundled install must reach the pack — and, with neither,
say what the user should press instead of dying as "No tokenizer registered".
"""

from __future__ import annotations

import pytest

from anki_miner.languages.ko import availability, tokenizer
from anki_miner.languages.tagger_provider import _build
from anki_miner.services import ko_model_installer


def _install_fake_pack(root) -> None:
    """Lay down a pack that :func:`ko_model_installer.is_installed` accepts."""
    model = ko_model_installer.ko_model_path(root)
    model.mkdir(parents=True, exist_ok=True)
    for name in ko_model_installer._MODEL_SENTINELS:
        (model / name).write_bytes(b"x")


class TestModelResolution:
    def test_the_installed_package_wins_and_lets_kiwi_resolve_itself(self, monkeypatch, tmp_path) -> None:
        # A pip install with [ko] keeps working exactly as before: Kiwi is
        # constructed with no model_path and finds kiwipiepy_model itself.
        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: object())
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)

        assert tokenizer.resolve_model_path() is None

    def test_the_pack_answers_when_the_package_is_absent(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)
        _install_fake_pack(ko_model_installer.ko_model_root())

        resolved = tokenizer.resolve_model_path()

        assert resolved == str(ko_model_installer.ko_model_path(ko_model_installer.ko_model_root()))

    def test_a_half_written_pack_is_not_accepted(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)
        model = ko_model_installer.ko_model_path(ko_model_installer.ko_model_root())
        model.mkdir(parents=True)
        (model / "sj.morph").write_bytes(b"x")  # the other sentinels never landed

        with pytest.raises(ImportError):
            tokenizer.resolve_model_path()

    def test_neither_names_the_in_app_download(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)

        with pytest.raises(ImportError) as excinfo:
            tokenizer.resolve_model_path()

        message = str(excinfo.value)
        assert "Download" in message
        assert "Mining Language" in message
        assert "pip install" not in message

    def test_the_provider_chains_the_missing_model_into_its_value_error(self, monkeypatch, tmp_path) -> None:
        # The contract every caller writes is ``except ValueError``; a bare
        # ImportError would escape it, so the reason has to arrive chained.
        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)

        with pytest.raises(ValueError) as excinfo:
            _build("ko")

        assert "No tokenizer registered" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ImportError)
        assert "Mining Language" in str(excinfo.value.__cause__)


class TestAvailability:
    def test_the_pack_alone_satisfies_the_model_requirement(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)
        _install_fake_pack(ko_model_installer.ko_model_root())

        assert availability.ko_missing_required_reason() is None

    def test_without_package_or_pack_the_reason_names_the_download(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)

        reason = availability.ko_missing_required_reason() or ""

        assert "Download" in reason
        assert "Mining Language" in reason
        assert "pip install" not in reason

    def test_a_missing_engine_still_points_at_the_extra(self, monkeypatch, tmp_path) -> None:
        # The pack carries the model, never the engine: without kiwipiepy there
        # is nothing to download in-app and pip is still the answer.
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy" else object())
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)
        _install_fake_pack(ko_model_installer.ko_model_root())

        reason = availability.ko_missing_required_reason() or ""

        assert "kiwipiepy" in reason
        assert 'pip install "anki-miner[ko]"' in reason

    def test_the_selector_offers_ko_once_the_pack_is_installed(self, monkeypatch, tmp_path) -> None:
        from anki_miner.gui.utils import language_choices

        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        monkeypatch.setattr(ko_model_installer.paths, "ANKI_MINER_HOME", tmp_path)
        _install_fake_pack(ko_model_installer.ko_model_root())

        assert "ko" in [code for code, _name in language_choices.available_mining_languages()]
