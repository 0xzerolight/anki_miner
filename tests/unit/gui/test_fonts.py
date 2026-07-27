"""The app's letterforms come from the operating system (decision D44-B).

The stylesheet used to ask for ``'Segoe UI', 'SF Pro Display', -apple-system,
BlinkMacSystemFont, sans-serif`` and, for the activity log and the progress
clock, ``'JetBrains Mono', 'Consolas'``. On Windows the first list resolved as
intended; everywhere else the font system silently substituted whatever it
liked, and the two monospace families exist on exactly one desktop each. What
these tests pin is that nothing hard-codes a family any more: the interface and
fixed-width faces are whatever *this* platform's font database names them, and
the Japanese face is one the database actually lists for Japanese.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QLabel  # noqa: E402

from anki_miner.gui.resources import get_resource_dir  # noqa: E402
from anki_miner.gui.resources.styles._variables import FONT_SIZES, TYPOGRAPHY  # noqa: E402
from anki_miner.gui.resources.styles.theme import Theme  # noqa: E402
from anki_miner.gui.utils import fonts as fonts_module  # noqa: E402
from anki_miner.gui.utils.fonts import (  # noqa: E402
    BUNDLED_JAPANESE_FILE,
    JAPANESE_BODY,
    JAPANESE_FEATURE,
    JAPANESE_PROPERTY,
    apply_japanese_font,
    font_family_variables,
    initialize_application_fonts,
    japanese_cell_font,
    japanese_line_spacing,
    make_japanese_font,
    make_scaled_font,
    make_scaled_monospace_font,
    reset_font_cache,
    resolved_families,
)

#: Every family the old stylesheet named by hand. None may be *requested* again.
_HARD_CODED = ("Segoe UI", "SF Pro Display", "BlinkMacSystemFont", "JetBrains Mono", "Consolas", "SF Mono")

#: The artifact pinned in anki_miner/gui/resources/fonts/PROVENANCE.md.
_BUNDLED_SHA256 = "dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073"


def _reset(font_scale: float = 1.0) -> None:
    """Reset Theme singleton to a clean state at the given scale."""
    Theme.initialize(
        active="light", favorites=("light", "dark"), user_dir=None, state_listener=None, font_scale=font_scale
    )


@pytest.fixture(autouse=True)
def reset_theme(qapp):
    """Reset Theme font scale to 1.0 before and after each test.

    The resolved families are dropped too, so a test that fakes the font
    database cannot leak its answer into the next one.
    """
    _reset(1.0)
    reset_font_cache()
    yield
    _reset(1.0)
    reset_font_cache()


class TestMakeScaledFontScale1:
    def test_pixel_size_unmodified(self):
        assert make_scaled_font(14).pixelSize() == 14

    def test_floor_at_one_pixel(self):
        assert make_scaled_font(1).pixelSize() == 1


class TestMakeScaledFontScale1Point5:
    def test_14px_becomes_21(self):
        _reset(1.5)
        assert make_scaled_font(14).pixelSize() == 21  # round(14 * 1.5) = 21

    def test_16px_becomes_24(self):
        _reset(1.5)
        assert make_scaled_font(16).pixelSize() == 24  # round(16 * 1.5) = 24


class TestMakeScaledFontScale2:
    def test_11px_becomes_22(self):
        _reset(2.0)
        assert make_scaled_font(11).pixelSize() == 22  # round(11 * 2.0) = 22


class TestMakeScaledFontWeight:
    def test_bold_weight_applied(self):
        font = make_scaled_font(14, QFont.Weight.Bold)
        assert font.weight() == QFont.Weight.Bold

    def test_default_weight_is_normal(self):
        font = make_scaled_font(14)
        assert font.weight() == QFont.Weight.Normal


class TestPlatformResolution:
    def test_interface_family_is_the_platform_general_font(self):
        expected = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        assert resolved_families().interface == expected

    def test_monospace_family_is_the_platform_fixed_font(self):
        expected = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        assert resolved_families().monospace == expected

    def test_no_resolved_family_is_one_of_the_hard_coded_names(self):
        # Unless this machine genuinely has one installed *and* the platform
        # picked it, in which case it came from the platform, not from a list.
        installed = set(QFontDatabase.families())
        families = resolved_families()
        for name in _HARD_CODED:
            if name in installed:
                continue
            assert families.interface != name
            assert families.monospace != name
            assert families.japanese != name

    def test_resolution_is_cached(self):
        assert resolved_families() is resolved_families()

    def test_reset_clears_the_cache_and_the_compiled_stylesheet(self):
        first = resolved_families()
        Theme.get_stylesheet("light")
        assert Theme._compiled_qss
        reset_font_cache()
        assert not Theme._compiled_qss
        assert resolved_families() is not first


class TestJapaneseResolution:
    def test_japanese_family_is_listed_for_japanese(self):
        listed = set(QFontDatabase.families(QFontDatabase.WritingSystem.Japanese))
        if not listed:
            pytest.skip("no Japanese-capable family on this machine")
        assert resolved_families().japanese in listed

    def test_vertical_writing_aliases_are_never_chosen(self):
        assert not resolved_families().japanese.startswith("@")

    def test_installed_preference_wins_over_the_bundled_face(self, monkeypatch):
        monkeypatch.setattr(fonts_module, "_japanese_families", lambda: ["Zzz Gothic", "Meiryo"])
        called = False

        def _never() -> str | None:
            nonlocal called
            called = True
            return "Noto Sans JP"

        monkeypatch.setattr(fonts_module, "_register_bundled_japanese", _never)
        reset_font_cache()
        assert resolved_families().japanese == "Meiryo"
        assert called is False

    def test_remaining_families_are_taken_in_a_deterministic_order(self, monkeypatch):
        monkeypatch.setattr(fonts_module, "_japanese_families", lambda: ["Zzz Gothic", "Aaa Mincho"])
        reset_font_cache()
        assert resolved_families().japanese == "Aaa Mincho"

    def test_bundled_face_is_used_when_the_machine_has_none(self, monkeypatch):
        monkeypatch.setattr(fonts_module, "_japanese_families", lambda: [])
        reset_font_cache()
        # The bundled OTF registers itself; the family name is whatever Qt read
        # out of the file, so assert it is a real registered family, not a literal.
        resolved = resolved_families().japanese
        assert resolved
        assert resolved in set(QFontDatabase.families())

    def test_a_missing_bundled_face_cannot_abort_startup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fonts_module, "_japanese_families", lambda: [])
        monkeypatch.setattr(fonts_module, "get_resource_dir", lambda: tmp_path)
        reset_font_cache()
        assert resolved_families().japanese == resolved_families().interface

    def test_a_corrupt_bundled_face_cannot_abort_startup(self, monkeypatch, tmp_path):
        (tmp_path / "fonts").mkdir()
        (tmp_path / "fonts" / BUNDLED_JAPANESE_FILE).write_bytes(b"not a font")
        monkeypatch.setattr(fonts_module, "_japanese_families", lambda: [])
        monkeypatch.setattr(fonts_module, "get_resource_dir", lambda: tmp_path)
        reset_font_cache()
        assert resolved_families().japanese == resolved_families().interface


class TestBundledAsset:
    """The fallback only helps if it actually ships."""

    def test_font_licence_and_provenance_are_on_disk(self):
        directory = get_resource_dir() / "fonts"
        assert (directory / BUNDLED_JAPANESE_FILE).is_file()
        assert (directory / "OFL.txt").is_file()
        assert (directory / "PROVENANCE.md").is_file()

    def test_the_bundled_font_is_the_pinned_artifact(self):
        digest = hashlib.sha256((get_resource_dir() / "fonts" / BUNDLED_JAPANESE_FILE).read_bytes()).hexdigest()
        assert digest == _BUNDLED_SHA256

    def test_the_font_loads_through_the_frozen_resource_resolver(self, monkeypatch, tmp_path):
        """``get_resource_dir()`` answers from ``sys._MEIPASS`` under PyInstaller.

        The patches are undone inside the test: with ``sys.frozen`` still set,
        the theme loader would go looking for JSON themes under the fake bundle
        and this module's teardown would blow up instead of the assertion.
        """
        meipass = tmp_path / "_MEI"
        target = meipass / "anki_miner" / "gui" / "resources" / "fonts"
        target.mkdir(parents=True)
        shutil.copy(get_resource_dir() / "fonts" / BUNDLED_JAPANESE_FILE, target / BUNDLED_JAPANESE_FILE)
        with monkeypatch.context() as frozen:
            frozen.setattr(sys, "frozen", True, raising=False)
            frozen.setattr(sys, "_MEIPASS", str(meipass), raising=False)
            frozen.setattr(fonts_module, "_japanese_families", lambda: [])
            reset_font_cache()
            assert get_resource_dir() == meipass / "anki_miner" / "gui" / "resources"
            assert resolved_families().japanese in set(QFontDatabase.families())
        reset_font_cache()


class TestScaledMonospaceFont:
    """The activity log and the progress clock (decision D44-B)."""

    def test_it_carries_the_platform_family_and_the_hint(self):
        font = make_scaled_monospace_font(FONT_SIZES.body_sm)
        assert font.family() == resolved_families().monospace
        assert font.styleHint() == QFont.StyleHint.Monospace

    def test_it_follows_the_text_size_setting(self):
        base = make_scaled_monospace_font(FONT_SIZES.body_sm).pixelSize()
        _reset(2.0)
        assert make_scaled_monospace_font(FONT_SIZES.body_sm).pixelSize() == 2 * base

    def test_weight_is_honoured(self):
        assert make_scaled_monospace_font(13, QFont.Weight.Bold).weight() == QFont.Weight.Bold


class TestJapaneseFonts:
    def test_it_follows_the_text_size_setting(self):
        base = make_japanese_font(FONT_SIZES.japanese_body).pixelSize()
        _reset(1.5)
        assert make_japanese_font(FONT_SIZES.japanese_body).pixelSize() > base

    def test_a_cell_font_carries_no_size_so_rows_do_not_grow(self):
        cell = japanese_cell_font()
        assert cell.family() == resolved_families().japanese
        assert cell.pixelSize() == -1

    def test_japanese_content_is_larger_than_the_surrounding_chrome(self):
        assert FONT_SIZES.japanese_body > FONT_SIZES.body
        assert FONT_SIZES.japanese_feature > FONT_SIZES.japanese_body

    def test_leading_is_looser_than_the_line_itself(self):
        assert TYPOGRAPHY.japanese_leading_percent > 100
        assert japanese_line_spacing() > 0


class TestStylesheetVariables:
    def test_the_stylesheet_names_what_the_service_resolved(self):
        families = resolved_families()
        variables = font_family_variables()
        assert variables["font-family-interface"] == f"'{families.interface}'"
        assert variables["font-family-mono"] == f"'{families.monospace}'"
        assert variables["font-family-japanese"] == f"'{families.japanese}'"

    def test_no_hard_coded_family_survives_in_the_compiled_stylesheet(self):
        installed = set(QFontDatabase.families())
        declarations = [
            line.strip()
            for line in Theme.get_stylesheet("light").splitlines()
            if line.strip().startswith("font-family:")
        ]
        assert declarations
        for name in _HARD_CODED:
            if name in installed:
                continue
            for declaration in declarations:
                assert name not in declaration, declaration

    def test_the_japanese_rules_are_reachable(self):
        """The old ``*[japanese="true"]`` rule was dead — nothing ever set it.

        These two replace it, and the values the stylesheet selects on are the
        exact values ``apply_japanese_font`` writes.
        """
        source = (get_resource_dir() / "styles" / "common.qss").read_text(encoding="utf-8")
        selectors = set(re.findall(r'\*\[japanese="([a-z]+)"\]', source))
        assert selectors == {JAPANESE_BODY, JAPANESE_FEATURE}
        assert 'japanese="true"' not in source

    def test_every_font_family_declaration_comes_from_a_variable(self):
        source = (get_resource_dir() / "styles" / "common.qss").read_text(encoding="utf-8")
        declarations = [line.strip() for line in source.splitlines() if line.strip().startswith("font-family:")]
        assert declarations
        for declaration in declarations:
            assert "${font-family-" in declaration, declaration


class TestJapaneseSurfaceMarking:
    def test_marking_sets_the_property_the_stylesheet_selects_on(self, qtbot):
        label = QLabel()
        qtbot.addWidget(label)
        apply_japanese_font(label, role="feature")
        assert label.property(JAPANESE_PROPERTY) == "feature"
        assert label.font().family() == resolved_families().japanese
        assert label.font().pixelSize() == make_japanese_font(FONT_SIZES.japanese_feature).pixelSize()

    def test_the_japanese_rule_outranks_the_interface_rule(self, qapp, qtbot):
        """A stylesheet beats ``setFont``; the property is the only way out."""
        plain = QLabel()
        marked = QLabel()
        qtbot.addWidget(plain)
        qtbot.addWidget(marked)
        apply_japanese_font(marked, role="body")
        stylesheet = qapp.styleSheet()
        Theme.apply_to_app(qapp)
        plain.show()
        marked.show()
        qapp.processEvents()
        try:
            assert marked.font().family() == resolved_families().japanese
            assert marked.font().pixelSize() > plain.font().pixelSize()
        finally:
            plain.hide()
            marked.hide()
            qapp.setStyleSheet(stylesheet)

    def test_an_unmarked_label_keeps_the_interface_font(self, qapp, qtbot):
        """Regression for the Chinese UI languages: only marked surfaces move.

        A global Japanese font would give Simplified and Traditional Chinese
        their kanji in Japanese shapes, which is the wrong form for both.
        """
        label = QLabel()
        qtbot.addWidget(label)
        stylesheet = qapp.styleSheet()
        Theme.apply_to_app(qapp)
        label.show()
        qapp.processEvents()
        try:
            assert label.property(JAPANESE_PROPERTY) is None
            assert label.font().family() == resolved_families().interface
        finally:
            label.hide()
            qapp.setStyleSheet(stylesheet)


class TestApplicationInitialization:
    def test_it_puts_the_platform_font_on_the_application(self, qapp):
        original = qapp.font()
        try:
            initialize_application_fonts(qapp)
            expected = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
            assert qapp.font().family() == expected.family()
        finally:
            qapp.setFont(original)

    def test_it_never_raises(self, qapp, monkeypatch):
        original = qapp.font()
        try:

            def _boom(*_args, **_kwargs):
                raise RuntimeError("font database unavailable")

            monkeypatch.setattr(fonts_module.QFontDatabase, "systemFont", _boom)
            reset_font_cache()
            initialize_application_fonts(qapp)  # must not raise
        finally:
            qapp.setFont(original)
