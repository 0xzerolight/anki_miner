"""th normalisation, key folding and the script gate."""

from __future__ import annotations

import unicodedata

from anki_miner.languages.th.normalize import fold_term_th, normalize_th
from anki_miner.languages.th.support import (
    ThaiDictKeyFolding,
    ThaiLookupStrategy,
    ThaiMinedFormPolicy,
    ThaiScriptSupport,
)

ZWSP = "\N{ZERO WIDTH SPACE}"


def test_sara_am_is_never_decomposed():
    # NFKC would split U+0E33 into U+0E4D + U+0E32 and break every dictionary key.
    assert normalize_th("น้ำ") == "น้ำ"
    assert unicodedata.normalize("NFKC", "น้ำ") != normalize_th("น้ำ")


def test_vowels_are_reordered_and_repeats_collapsed():
    assert normalize_th("เเปลก") == "แปลก"  # SARA E + SARA E -> SARA AE
    assert normalize_th("นานาาา") == "นานา"
    assert normalize_th("นํา") == "นำ"


def test_repeated_consonants_are_left_alone():
    assert normalize_th("มากกกก") == "มากกกก"


def test_zero_width_space_survives_normalisation():
    # It is the tokenizer's word-break hint; the stored sentence keeps it.
    assert ZWSP in normalize_th("สวัสดี" + ZWSP + "ครับ")


def test_whitespace_is_collapsed():
    assert normalize_th("ไม่รู้ค่ะ   นะคะ") == "ไม่รู้ค่ะ นะคะ"


def test_fold_term_is_idempotent_and_symmetric():
    for term in ("น้ำ", "เเปลก", "นํา", "กรุงเทพฯ", "ดีมาก"):
        folded = fold_term_th(term)
        assert fold_term_th(folded) == folded


def test_dedup_fold_strips_format_characters():
    folding = ThaiDictKeyFolding()
    assert folding.dedup_fold("สวัสดี" + ZWSP + "ครับ") == folding.dedup_fold("สวัสดีครับ")


def test_reading_fold_casefolds_latin_paiboon():
    folding = ThaiDictKeyFolding()
    assert folding.fold_reading("Sà-wàt-dii") == folding.fold_reading("sà-wàt-dii")
    assert folding.fold_reading(None) is None


def test_importing_normalize_alone_never_writes_into_home(tmp_path):
    # M2: `from pythainlp.util import reorder_vowels` alone creates
    # $HOME/pythainlp-data unless _engine ran first. This test imports no
    # tokenizer, which is the whole point.
    import os
    import subprocess
    import sys

    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("PYTHAINLP_READ_ONLY", None)
    env.pop("PYTHAINLP_OFFLINE", None)
    code = "from anki_miner.languages.th.normalize import normalize_th; normalize_th('เเปลก')"
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
    assert list(home.iterdir()) == []


def test_script_gate_accepts_thai_letters_and_rejects_marks_and_digits():
    script = ThaiScriptSupport()
    assert script.filter_options() == ()
    assert script.contains_target_script("น้ำ")
    assert script.contains_target_script("กรุงเทพฯ")
    assert not script.contains_target_script("๒๕๖๗")
    assert not script.contains_target_script("ๆ")
    assert not script.contains_target_script("Netflix")


def test_mined_form_is_the_surface_with_its_trailing_paiyannoi():
    policy = ThaiMinedFormPolicy()
    assert policy.mined_form("NOUN", "", "กรุงเทพฯ", "กรุงเทพฯ") == "กรุงเทพฯ"
    assert policy.mined_form("VERB", "", "กินข้าว", "กินข้าว") == "กินข้าว"


def test_lookup_ladder_offers_the_stripped_and_normalised_variants():
    ladder = ThaiLookupStrategy()
    assert ("กรุงเทพ", 0) in ladder.candidates("กรุงเทพฯ", "", None)
    assert ladder.candidates("ดีมาก", "", None) == []
