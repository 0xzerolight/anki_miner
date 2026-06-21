"""Tests for the card-styling composition core (Issue #44)."""

from anki_miner.services.dictionary.card_styling import (
    BEGIN_MARKER_PREFIX,
    END_MARKER,
    apply_managed_block,
    build_managed_block,
    detect_applied_preset,
    load_default_card_css,
    strip_managed_block,
)

# A legacy BEGIN marker (no recorded preset id) — the form written before the
# marker started embedding ``preset=<id>``. Used to verify back-compat.
LEGACY_BEGIN = "/* === ANKI MINER DICT STYLES (managed — do not edit) === */"


class TestLoadDefaultCss:
    def test_returns_nonempty_with_sentinel_selector(self):
        css = load_default_card_css()
        assert css.strip()
        assert ".yomitan-glossary" in css


class TestBuildManagedBlock:
    def test_includes_both_markers(self):
        block = build_managed_block(preset="default", custom_css="")
        assert block.startswith(BEGIN_MARKER_PREFIX)
        assert block.rstrip().endswith(END_MARKER)

    def test_marker_records_preset_id(self):
        block = build_managed_block(preset="minimal", custom_css="")
        assert "preset=minimal" in block.splitlines()[0]

    def test_default_included_when_enabled(self):
        block = build_managed_block(preset="default", custom_css="")
        assert ".yomitan-glossary" in block

    def test_default_omitted_when_none(self):
        block = build_managed_block(preset="none", custom_css="")
        # No preset body — just the two markers back to back.
        assert ".yomitan-glossary" not in block
        assert block.splitlines()[0].startswith(BEGIN_MARKER_PREFIX)
        assert block.splitlines()[1] == END_MARKER

    def test_named_preset_used(self):
        minimal = build_managed_block(preset="minimal", custom_css="")
        none_block = build_managed_block(preset="none", custom_css="")
        assert minimal != none_block
        assert ".yomitan-glossary" in minimal
        assert len(minimal) > len(none_block) + len(".yomitan-glossary")

    def test_custom_css_appended(self):
        block = build_managed_block(preset="none", custom_css=".x{color:red}")
        assert ".x{color:red}" in block
        assert block.startswith(BEGIN_MARKER_PREFIX) and block.rstrip().endswith(END_MARKER)

    def test_both_empty_still_emits_markers(self):
        block = build_managed_block(preset="none", custom_css="   ")
        assert BEGIN_MARKER_PREFIX in block and END_MARKER in block


class TestDetectAppliedPreset:
    def test_none_when_no_block(self):
        assert detect_applied_preset(".card{font-size:20px}") is None
        assert detect_applied_preset("") is None

    def test_returns_recorded_preset_id(self):
        block = build_managed_block(preset="minimal", custom_css="")
        assert detect_applied_preset(block) == "minimal"

    def test_recorded_id_survives_surrounding_css(self):
        block = build_managed_block(preset="yomitan-classic", custom_css=".x{}")
        existing = apply_managed_block(".before{}\n\n.after{}", block)
        assert detect_applied_preset(existing) == "yomitan-classic"

    def test_legacy_block_detected_as_empty_string(self):
        legacy = f"{LEGACY_BEGIN}\n.x{{}}\n{END_MARKER}"
        # Present (not None) but unknown source → "".
        assert detect_applied_preset(legacy) == ""

    def test_legacy_block_with_preset_in_body_not_misdetected(self):
        # User custom CSS containing the literal ``preset=`` must NOT be read as
        # the recorded preset id — only the BEGIN marker line is authoritative.
        legacy = f'{LEGACY_BEGIN}\n.x{{content:"preset=evil"}}\n{END_MARKER}'
        assert detect_applied_preset(legacy) == ""

    def test_current_id_wins_over_preset_decoy_in_body(self):
        block = build_managed_block(preset="minimal", custom_css='.x{content:"preset=evil"}')
        assert detect_applied_preset(block) == "minimal"


class TestApplyManagedBlock:
    def test_first_time_appends_after_user_css(self):
        block = build_managed_block(preset="none", custom_css=".x{color:red}")
        out = apply_managed_block(".card{font-size:20px}", block)
        assert out.startswith(".card{font-size:20px}")
        assert BEGIN_MARKER_PREFIX in out and END_MARKER in out

    def test_into_empty_css(self):
        block = build_managed_block(preset="none", custom_css=".x{}")
        out = apply_managed_block("", block)
        assert out.strip().startswith(BEGIN_MARKER_PREFIX)
        assert out.strip().endswith(END_MARKER)

    def test_idempotent_no_duplicate_block(self):
        block = build_managed_block(preset="default", custom_css=".x{}")
        once = apply_managed_block(".card{}", block)
        twice = apply_managed_block(once, block)
        assert once == twice
        assert once.count(BEGIN_MARKER_PREFIX) == 1
        assert once.count(END_MARKER) == 1

    def test_replace_preserves_surrounding_user_css(self):
        block_v1 = build_managed_block(preset="none", custom_css=".v1{}")
        block_v2 = build_managed_block(preset="none", custom_css=".v2{}")
        existing = apply_managed_block(".before{}", block_v1) + "\n.after{}"
        out = apply_managed_block(existing, block_v2)
        assert ".before{}" in out
        assert ".after{}" in out
        assert ".v2{}" in out
        assert ".v1{}" not in out
        assert out.count(BEGIN_MARKER_PREFIX) == 1

    def test_replace_legacy_block_in_place(self):
        # A pre-existing legacy block must be replaced (not duplicated) when a
        # new preset-tagged block is applied.
        legacy = f".before{{}}\n\n{LEGACY_BEGIN}\n.old{{}}\n{END_MARKER}"
        block = build_managed_block(preset="minimal", custom_css="")
        out = apply_managed_block(legacy, block)
        assert out.count(BEGIN_MARKER_PREFIX) == 1
        assert ".before{}" in out
        assert ".old{}" not in out
        assert detect_applied_preset(out) == "minimal"

    def test_half_marker_appends_fresh_not_corrupt(self):
        # User deleted the END_MARKER — half a block. Must not be treated as a
        # valid block; append a fresh one and leave the orphan marker in place.
        block = build_managed_block(preset="none", custom_css=".x{}")
        broken = f".card{{}}\n{LEGACY_BEGIN}\n.orphan{{}}"
        out = apply_managed_block(broken, block)
        assert ".orphan{}" in out  # orphan content preserved, not destroyed
        assert out.rstrip().endswith(END_MARKER)
        assert out.count(END_MARKER) == 1

    def test_multiple_blocks_collapse_to_one(self):
        block = build_managed_block(preset="none", custom_css=".x{}")
        doubled = f"{block}\n\n.mid{{}}\n\n{block}"
        out = apply_managed_block(doubled, block)
        assert out.count(BEGIN_MARKER_PREFIX) == 1
        assert ".mid{}" in out


class TestStripManagedBlock:
    def test_removes_block_keeps_surrounding(self):
        block = build_managed_block(preset="default", custom_css=".x{}")
        existing = apply_managed_block(".before{}\n\n.after{}", block)
        out = strip_managed_block(existing)
        assert BEGIN_MARKER_PREFIX not in out
        assert END_MARKER not in out
        assert ".before{}" in out
        assert ".after{}" in out

    def test_strips_legacy_block(self):
        legacy = f".before{{}}\n\n{LEGACY_BEGIN}\n.old{{}}\n{END_MARKER}\n\n.after{{}}"
        out = strip_managed_block(legacy)
        assert BEGIN_MARKER_PREFIX not in out
        assert ".old{}" not in out
        assert ".before{}" in out and ".after{}" in out

    def test_noop_when_absent(self):
        original = ".card{font-size:20px}\n"
        assert strip_managed_block(original) == original

    def test_block_only_strips_to_empty(self):
        block = build_managed_block(preset="default", custom_css="")
        out = strip_managed_block(apply_managed_block("", block))
        assert out == ""

    def test_apply_then_strip_round_trips_to_original(self):
        block = build_managed_block(preset="default", custom_css=".x{color:red}")
        original = ".card{font-size:20px}"
        out = strip_managed_block(apply_managed_block(original, block))
        assert out.strip() == original.strip()
