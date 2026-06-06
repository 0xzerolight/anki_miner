"""Tests for get_variable_dict font_scale parameter in _variables.py."""

from anki_miner.gui.resources.styles._variables import BORDER_RADIUS, FONT_SIZES, SPACING, get_variable_dict


class TestGetVariableDictDefault:
    """Default (scale=1.0) must be byte-identical to the previous unscaled output."""

    def test_font_size_body_unscaled(self):
        assert get_variable_dict()["font-size-body"] == str(FONT_SIZES.body)
        assert get_variable_dict()["font-size-body"] == "14"

    def test_font_size_h1_unscaled(self):
        assert get_variable_dict()["font-size-h1"] == "24"

    def test_font_size_small_unscaled(self):
        assert get_variable_dict()["font-size-small"] == "11"

    def test_spacing_unchanged_at_default(self):
        d = get_variable_dict()
        assert d["spacing-md"] == str(SPACING.md)
        assert d["spacing-xs"] == str(SPACING.xs)

    def test_border_radius_unchanged_at_default(self):
        d = get_variable_dict()
        assert d["border-radius-large"] == str(BORDER_RADIUS.large)
        assert d["border-radius-small"] == str(BORDER_RADIUS.small)

    def test_explicit_1_0_equals_default(self):
        assert get_variable_dict(1.0) == get_variable_dict()


class TestGetVariableDictScale15:
    """scale=1.5 must multiply only font-size-* entries."""

    def test_font_size_body_scaled(self):
        # round(14 * 1.5) == 21
        assert get_variable_dict(1.5)["font-size-body"] == "21"

    def test_font_size_h1_scaled(self):
        # round(24 * 1.5) == 36
        assert get_variable_dict(1.5)["font-size-h1"] == "36"

    def test_spacing_unchanged(self):
        d = get_variable_dict(1.5)
        assert d["spacing-md"] == str(SPACING.md)
        assert d["spacing-lg"] == str(SPACING.lg)

    def test_border_radius_unchanged(self):
        d = get_variable_dict(1.5)
        assert d["border-radius-large"] == str(BORDER_RADIUS.large)
        assert d["border-radius-default"] == str(BORDER_RADIUS.default)


class TestGetVariableDictScale20:
    """scale=2.0 must double all font sizes."""

    def test_font_size_small_doubled(self):
        # round(11 * 2.0) == 22
        assert get_variable_dict(2.0)["font-size-small"] == "22"

    def test_font_size_body_doubled(self):
        assert get_variable_dict(2.0)["font-size-body"] == "28"

    def test_font_size_h1_doubled(self):
        assert get_variable_dict(2.0)["font-size-h1"] == "48"


class TestGetVariableDictFloor:
    """Scaled values must never drop below 1px."""

    def test_floor_at_1px(self):
        # scale=1.0, smallest is 11px — no value goes below 1, but verify formula.
        d = get_variable_dict(1.0)
        for key, val in d.items():
            if key.startswith("font-size-"):
                assert int(val) >= 1, f"{key}={val} dropped below 1px"

    def test_sub_one_scale_stays_at_least_1px(self):
        # scale=0.5 must shrink fonts but never below the 1px floor.
        d = get_variable_dict(0.5)
        for key, val in d.items():
            if key.startswith("font-size-"):
                assert int(val) >= 1, f"{key}={val} dropped below 1px at scale=0.5"

    def test_sub_one_scale_shrinks_body(self):
        # round(14 * 0.5) == 7
        assert get_variable_dict(0.5)["font-size-body"] == "7"
