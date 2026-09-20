"""Persian word audio: the Edge read-aloud leg, by profile data alone.

Google Translate has no Persian voice -- ``gTTS.tts_langs()`` lists ``ar`` and
``ur`` but no ``fa`` -- so ``gtts_lang`` is "" and the synthetic leg is the
``edgetts`` kind the seam landed (spec D14). ``service_factory`` builds that leg
from ``edge_voice`` alone; this module adds no code to the chain.

The ladder offers the card front and nothing else. Persian writes no reading:
``expression_reading`` stays "", and the romanisation the card DOES carry
(``reading_romanized``, from the dictionary's head line) is Latin -- handing it
to a Persian voice would have it read an English-looking string aloud. Both
cache prefixes are namespaced anyway, because the stem doubles as the Anki media
filename; both are inert while no Google leg exists for fa.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults


def fa_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """``[(mined_form, mined_form)]``: the card front, in both slots."""
    term = str(getattr(word, "mined_form", "") or "")
    return [(term, term)] if term else []


def fa_speakable(term: str, reading: str) -> str | None:
    """What a Persian voice may speak for a ladder pair: the term, or nothing.

    ``reading`` is ignored rather than preferred (the spaCy rule): the only Latin
    string in reach of a Persian card is its romanisation, which is for the
    learner's eye, not for the voice.
    """
    del reading
    return term or None


FA_AUDIO = AudioDefaults(
    gtts_lang="",
    cache_stem_prefix="googletts_fa",
    sentence_cache_stem_prefix="sentencetts_fa",
    custom_fetcher_language="fa",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="edgetts"),),
    candidates=fa_audio_candidates,
    speakable=fa_speakable,
    edge_voice="fa-IR-DilaraNeural",
)
