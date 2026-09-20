"""Persian word audio: the Edge read-aloud leg as profile data (spec C.2, D14).

Google Translate has no Persian voice, so fa is the seam's first default consumer. D14 itself is
enforced centrally by ``test_edge_voice_contract.py``, which iterates the registry -- fa joins that
sweep when Task 14 registers it. This file pins fa's own values. Every Persian character is a
\\N{NAME} escape (LEAD-BRIEF section 3).
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.fa.audio import FA_AUDIO, fa_audio_candidates, fa_speakable

_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_KEHEH = "\N{ARABIC LETTER KEHEH}"

KETAB = _KEHEH + _TEH + _ALEF + _BEH  # ketab, "book"
#: Its wty romanisation, escaped so no combining mark can creep into the file.
KETAB_ROMAN = "ket" + "\N{LATIN SMALL LETTER A WITH CIRCUMFLEX}" + "b"
#: The seam's own voice-name shape (test_edge_voice_contract.py).
_VOICE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural$")


class TestDefaults:
    def test_persian_has_no_google_voice(self):
        assert FA_AUDIO.gtts_lang == ""
        assert FA_AUDIO.resolved_gtts_lang(AnkiMinerConfig(language="fa")) == ""

    def test_the_edge_leg_is_the_default_and_names_a_voice(self):
        assert _VOICE.fullmatch(FA_AUDIO.edge_voice), FA_AUDIO.edge_voice
        assert FA_AUDIO.edge_voice.startswith("fa-IR-")
        kinds = [(entry.kind, entry.enabled) for entry in FA_AUDIO.default_chain]
        assert kinds == [("edgetts", True)]

    def test_both_cache_stems_are_namespaced(self):
        # The stem doubles as the Anki media filename, so no fa file can collide
        # with another language's -- inert though both are while no Google leg exists.
        assert FA_AUDIO.cache_stem_prefix.endswith("_fa")
        assert FA_AUDIO.sentence_cache_stem_prefix.endswith("_fa")
        assert FA_AUDIO.custom_fetcher_language == "fa"
        assert FA_AUDIO.papago_speaker is None


class TestLadder:
    def test_the_ladder_is_the_card_front_alone(self):
        assert fa_audio_candidates(SimpleNamespace(mined_form=KETAB)) == [(KETAB, KETAB)]

    def test_a_word_with_no_front_offers_nothing(self):
        assert fa_audio_candidates(SimpleNamespace(mined_form="")) == []
        assert fa_audio_candidates(SimpleNamespace()) == []

    def test_the_romanisation_is_never_synthesised(self):
        """``reading_romanized`` is Latin; a voice fed it would read English at a Persian card."""
        word = SimpleNamespace(mined_form=KETAB, expression_reading=KETAB_ROMAN, reading_romanized=KETAB_ROMAN)
        assert fa_audio_candidates(word) == [(KETAB, KETAB)]
        assert fa_speakable(KETAB, KETAB_ROMAN) == KETAB

    def test_nothing_speakable_is_none(self):
        assert fa_speakable("", "") is None
        assert fa_speakable("", KETAB_ROMAN) is None

    def test_the_profile_carries_both_callables(self):
        assert FA_AUDIO.candidates is fa_audio_candidates
        assert FA_AUDIO.speakable is fa_speakable
