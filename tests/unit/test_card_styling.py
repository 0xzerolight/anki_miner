"""Tests for the card-styling composition core (Issue #44)."""

from anki_miner.services.dictionary.card_styling import (
    BEGIN_MARKER_PREFIX,
    END_MARKER,
    apply_managed_block,
    build_managed_block,
    has_managed_block,
    strip_managed_block,
)

# Legacy BEGIN markers the migration must still match for replace/strip:
# the original ``managed — do not edit`` form and the preset-era
# ``managed; preset=<id>`` form. The marker regex is byte-stable across both.
LEGACY_PLAIN_BEGIN = "/* === ANKI MINER DICT STYLES (managed — do not edit) === */"
LEGACY_PRESET_BEGIN = "/* === ANKI MINER DICT STYLES (managed; preset=default; do not edit) === */"

# A hook present in the always-on universal stylesheet.
GLOSSARY_HOOK = ".yomitan-glossary"


class TestBuildManagedBlock:
    def test_includes_both_markers(self):
        block = build_managed_block(custom_css="", dict_css="")
        assert block.startswith(BEGIN_MARKER_PREFIX)
        assert block.rstrip().endswith(END_MARKER)

    def test_marker_has_no_preset_segment(self):
        # The static marker no longer records a preset id.
        assert "preset=" not in build_managed_block(custom_css="", dict_css="").splitlines()[0]

    def test_always_includes_universal_base(self):
        # The universal sheet is the always-on base, even with empty inputs.
        assert GLOSSARY_HOOK in build_managed_block(custom_css="", dict_css="")

    def test_dict_css_included(self):
        block = build_managed_block(custom_css="", dict_css='[data-dictionary="X"]{color:red}')
        assert '[data-dictionary="X"]{color:red}' in block

    def test_custom_css_appended(self):
        block = build_managed_block(custom_css=".x{color:red}", dict_css="")
        assert ".x{color:red}" in block
        assert block.startswith(BEGIN_MARKER_PREFIX) and block.rstrip().endswith(END_MARKER)

    def test_order_is_base_then_dict_then_custom(self):
        block = build_managed_block(custom_css="/*CUSTOMMARK*/", dict_css="/*DICTMARK*/")
        i_base = block.index(GLOSSARY_HOOK)
        i_dict = block.index("/*DICTMARK*/")
        i_custom = block.index("/*CUSTOMMARK*/")
        assert i_base < i_dict < i_custom

    def test_empty_inputs_still_emit_markers(self):
        block = build_managed_block(custom_css="   ", dict_css="  ")
        assert BEGIN_MARKER_PREFIX in block and END_MARKER in block

    def test_marker_like_text_in_inputs_is_inert(self):
        # No preset parsing remains, so 'preset=' inside CSS is harmless; the block
        # is still locatable.
        block = build_managed_block(custom_css='.x{content:"preset=evil"}', dict_css="")
        assert has_managed_block(block)


class TestHasManagedBlock:
    def test_false_when_no_block(self):
        assert not has_managed_block(".card{font-size:20px}")
        assert not has_managed_block("")

    def test_true_for_built_block(self):
        assert has_managed_block(build_managed_block(custom_css="", dict_css=""))

    def test_true_for_legacy_plain_block(self):
        assert has_managed_block(f"{LEGACY_PLAIN_BEGIN}\n.x{{}}\n{END_MARKER}")

    def test_true_for_legacy_preset_block(self):
        # The migration relies on the old preset= block still matching.
        assert has_managed_block(f"{LEGACY_PRESET_BEGIN}\n.x{{}}\n{END_MARKER}")


class TestApplyManagedBlock:
    def test_first_time_appends_after_user_css(self):
        block = build_managed_block(custom_css=".x{color:red}", dict_css="")
        out = apply_managed_block(".card{font-size:20px}", block)
        assert out.startswith(".card{font-size:20px}")
        assert BEGIN_MARKER_PREFIX in out and END_MARKER in out

    def test_into_empty_css(self):
        block = build_managed_block(custom_css=".x{}", dict_css="")
        out = apply_managed_block("", block)
        assert out.strip().startswith(BEGIN_MARKER_PREFIX)
        assert out.strip().endswith(END_MARKER)

    def test_idempotent_no_duplicate_block(self):
        block = build_managed_block(custom_css=".x{}", dict_css="")
        once = apply_managed_block(".card{}", block)
        twice = apply_managed_block(once, block)
        assert once == twice
        assert once.count(BEGIN_MARKER_PREFIX) == 1
        assert once.count(END_MARKER) == 1

    def test_replace_preserves_surrounding_user_css(self):
        block_v1 = build_managed_block(custom_css=".v1{}", dict_css="")
        block_v2 = build_managed_block(custom_css=".v2{}", dict_css="")
        existing = apply_managed_block(".before{}", block_v1) + "\n.after{}"
        out = apply_managed_block(existing, block_v2)
        assert ".before{}" in out
        assert ".after{}" in out
        assert ".v2{}" in out
        assert ".v1{}" not in out
        assert out.count(BEGIN_MARKER_PREFIX) == 1

    def test_replace_legacy_preset_block_in_place(self):
        # The migration case: an old preset-tagged block is replaced (not
        # duplicated) when the new universal block is applied.
        legacy = f".before{{}}\n\n{LEGACY_PRESET_BEGIN}\n.old{{}}\n{END_MARKER}"
        block = build_managed_block(custom_css="", dict_css="")
        out = apply_managed_block(legacy, block)
        assert out.count(BEGIN_MARKER_PREFIX) == 1
        assert ".before{}" in out
        assert ".old{}" not in out
        assert has_managed_block(out)

    def test_half_marker_appends_fresh_not_corrupt(self):
        # User deleted the END_MARKER — half a block. Must not be treated as a
        # valid block; append a fresh one and leave the orphan marker in place.
        block = build_managed_block(custom_css=".x{}", dict_css="")
        broken = f".card{{}}\n{LEGACY_PLAIN_BEGIN}\n.orphan{{}}"
        out = apply_managed_block(broken, block)
        assert ".orphan{}" in out  # orphan content preserved, not destroyed
        assert out.rstrip().endswith(END_MARKER)
        assert out.count(END_MARKER) == 1

    def test_multiple_blocks_collapse_to_one(self):
        block = build_managed_block(custom_css=".x{}", dict_css="")
        doubled = f"{block}\n\n.mid{{}}\n\n{block}"
        out = apply_managed_block(doubled, block)
        assert out.count(BEGIN_MARKER_PREFIX) == 1
        assert ".mid{}" in out


class TestStripManagedBlock:
    def test_removes_block_keeps_surrounding(self):
        block = build_managed_block(custom_css=".x{}", dict_css="")
        existing = apply_managed_block(".before{}\n\n.after{}", block)
        out = strip_managed_block(existing)
        assert BEGIN_MARKER_PREFIX not in out
        assert END_MARKER not in out
        assert ".before{}" in out
        assert ".after{}" in out

    def test_strips_legacy_preset_block(self):
        legacy = f".before{{}}\n\n{LEGACY_PRESET_BEGIN}\n.old{{}}\n{END_MARKER}\n\n.after{{}}"
        out = strip_managed_block(legacy)
        assert BEGIN_MARKER_PREFIX not in out
        assert ".old{}" not in out
        assert ".before{}" in out and ".after{}" in out

    def test_noop_when_absent(self):
        original = ".card{font-size:20px}\n"
        assert strip_managed_block(original) == original

    def test_block_only_strips_to_empty(self):
        block = build_managed_block(custom_css="", dict_css="")
        out = strip_managed_block(apply_managed_block("", block))
        assert out == ""

    def test_apply_then_strip_round_trips_to_original(self):
        block = build_managed_block(custom_css=".x{color:red}", dict_css="")
        original = ".card{font-size:20px}"
        out = strip_managed_block(apply_managed_block(original, block))
        assert out.strip() == original.strip()
