"""The zh stack reports why it is unavailable, and never imports its extra eagerly."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from anki_miner.languages.zh import availability

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class TestUnavailableReason:
    def test_no_missing_package_means_no_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "_installed", lambda _name: True)
        assert availability.zh_unavailable_reason() is None

    def test_a_missing_package_is_named_with_the_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "_installed", lambda name: name != "opencc")
        reason = availability.zh_unavailable_reason()
        assert reason is not None
        assert "opencc" in reason
        assert "anki-miner[zh]" in reason

    def test_every_missing_package_is_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "_installed", lambda _name: False)
        reason = availability.zh_unavailable_reason() or ""
        for package in availability.ZH_REQUIRED_PACKAGES + availability.ZH_OPTIONAL_PACKAGES:
            assert package in reason

    def test_an_optional_package_alone_is_not_an_availability_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenCC absent degrades variants; it must not take zh off the menu.

        ``zh_unavailable_reason`` still names it — a full-stack consumer wants
        the whole list — but the profile's gate reads required packages only.
        """
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "opencc" else object())
        assert availability.zh_missing_required_reason() is None
        assert "opencc" in (availability.zh_unavailable_reason() or "")

    def test_a_missing_required_package_gates_the_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "jieba" else object())
        reason = availability.zh_missing_required_reason() or ""
        assert "jieba" in reason
        assert "opencc" not in reason
        assert "anki-miner[zh]" in reason

    def test_the_probe_reads_the_real_environment(self) -> None:
        # _installed answers from find_spec, so an installed stdlib module is a
        # true probe and a nonsense name is a false one — no import executed.
        assert availability._installed("json") is True
        assert availability._installed("definitely_not_a_real_package_zzz") is False


def test_zh_modules_never_import_the_extra_at_module_level() -> None:
    # get_profile("zh") must build with no extra installed, so every jieba /
    # pypinyin / opencc import has to sit inside a function body.
    package_dir = PROJECT_ROOT / "anki_miner" / "languages" / "zh"
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert not ({"jieba", "pypinyin", "opencc"} & set(names)), f"{path.name} imports an extra eagerly"
