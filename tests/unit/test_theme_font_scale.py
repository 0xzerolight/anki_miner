"""Tests for Theme font-scale plumbing (set_font_scale, get_font_scale, initialize)."""

from __future__ import annotations

from anki_miner.gui.resources.styles.theme import Theme


def _reset(font_scale: float = 1.0) -> None:
    """Reset the Theme singleton to a clean state with shipped themes."""
    Theme.initialize(
        active="light", favorites=("light", "dark"), user_dir=None, state_listener=None, font_scale=font_scale
    )


class TestGetFontScale:
    def test_default_is_1_0(self):
        _reset()
        assert Theme.get_font_scale() == 1.0

    def test_initialize_sets_font_scale(self):
        _reset(font_scale=1.5)
        assert Theme.get_font_scale() == 1.5

    def test_initialize_resets_to_default_when_omitted(self):
        _reset(font_scale=1.5)
        _reset()  # no font_scale arg → defaults to 1.0
        assert Theme.get_font_scale() == 1.0


class TestSetFontScale:
    def test_set_and_get(self):
        _reset()
        Theme.set_font_scale(1.5)
        assert Theme.get_font_scale() == 1.5

    def test_clamp_above_max(self):
        _reset()
        Theme.set_font_scale(5.0)
        assert Theme.get_font_scale() == 2.0

    def test_clamp_below_min(self):
        _reset()
        Theme.set_font_scale(0.1)
        assert Theme.get_font_scale() == 0.5

    def test_sub_one_scale_unchanged(self):
        _reset()
        Theme.set_font_scale(0.75)
        assert Theme.get_font_scale() == 0.75

    def test_clamp_at_exact_bounds(self):
        _reset()
        Theme.set_font_scale(0.5)
        assert Theme.get_font_scale() == 0.5
        Theme.set_font_scale(2.0)
        assert Theme.get_font_scale() == 2.0

    def test_set_font_scale_invalidates_cache(self):
        _reset()
        # Populate the cache for "light".
        _ = Theme.get_stylesheet("light")
        assert "light" in Theme._compiled_qss
        # Changing the scale must clear the cache.
        Theme.set_font_scale(1.8)
        assert "light" not in Theme._compiled_qss

    def test_set_same_value_does_not_clear_cache(self):
        _reset()
        _ = Theme.get_stylesheet("light")
        assert "light" in Theme._compiled_qss
        Theme.set_font_scale(1.0)  # same value — should be a no-op
        assert "light" in Theme._compiled_qss


class TestInitializeFontScale:
    def test_initialize_clamps_above_max(self):
        Theme.initialize(active="light", favorites=("light", "dark"), font_scale=10.0)
        assert Theme.get_font_scale() == 2.0

    def test_initialize_clamps_below_min(self):
        Theme.initialize(active="light", favorites=("light", "dark"), font_scale=0.1)
        assert Theme.get_font_scale() == 0.5

    def test_initialize_accepts_min_boundary(self):
        Theme.initialize(active="light", favorites=("light", "dark"), font_scale=0.5)
        assert Theme.get_font_scale() == 0.5


class TestStylesheetScaling:
    """Behavioral test: scaled stylesheet must contain larger font-size values."""

    def test_scaled_stylesheet_has_larger_font_sizes(self):
        import re

        _reset(font_scale=1.0)
        baseline = Theme.get_stylesheet("light")

        _reset(font_scale=2.0)
        scaled = Theme.get_stylesheet("light")

        # Extract all font-size: <n>px values from each stylesheet.
        def font_sizes(qss: str) -> set[int]:
            return {int(m) for m in re.findall(r"font-size:\s*(\d+)px", qss)}

        baseline_sizes = font_sizes(baseline)
        scaled_sizes = font_sizes(scaled)

        # Baseline must contain 14px (body), scaled must contain 28px (body * 2).
        assert 14 in baseline_sizes, f"baseline font sizes: {sorted(baseline_sizes)}"
        assert 28 in scaled_sizes, f"scaled font sizes: {sorted(scaled_sizes)}"
        # Every font size in scaled must be >= its baseline counterpart.
        # The max in scaled should be strictly larger than the max in baseline.
        assert max(scaled_sizes) > max(baseline_sizes)

    def test_scale_1_0_variable_dict_identical_to_default(self):
        """get_variable_dict(1.0) must equal get_variable_dict() (default arg)."""
        from anki_miner.gui.resources.styles._variables import get_variable_dict

        assert get_variable_dict(1.0) == get_variable_dict()
