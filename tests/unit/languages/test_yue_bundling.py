"""yue is seeded and smoked by the release workflow, and its smoke line mines."""

from __future__ import annotations

from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

RELEASE = (Path(__file__).parents[3] / ".github" / "workflows" / "release.yml").read_text("utf-8")


def test_the_seed_list_and_the_smoke_langs_both_carry_yue():
    seed = next(line for line in RELEASE.splitlines() if "fetch_language_pack_seeds.py" in line)
    smoke = next(line for line in RELEASE.splitlines() if "BUNDLE_SMOKE_LANGS:" in line)
    assert " yue" in seed
    assert seed.rstrip().endswith("asr")  # the ASR pack stays last in the seed list
    assert " yue" in smoke


def test_the_smoke_line_mines_a_word_and_a_reading():
    profile = get_profile("yue")
    config = switch_language(AnkiMinerConfig(), "yue")
    parser = profile.create_parser(config)

    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text=profile.smoke_sentence, index=0, location_label="smoke")], False
    )

    assert words
    # gui/app.py's bundled smoke asserts exactly this for a profile with reading
    # support and no stress_marks capability.
    assert any(word.expression_reading for word in words)
