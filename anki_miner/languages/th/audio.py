"""th expression- and sentence-audio defaults (spec C.3).

Google Translate has a Thai voice (verified live), and it speaks Thai script
directly, so the default chain is the one synthetic entry the app already
carries. The Paiboon field is never fed to TTS: it is Latin, and a th-TH voice
reads Latin letters as Latin letters.

No Edge read-aloud leg: ``edge_voice`` is for a language with no Google voice
(fa, sl) or one a user may add by hand (he, yue). Thai has a Google voice, and
the seam's contract is that a language declaring no voice gets no Edge leg
offered in Settings -> Word Audio.

Commons carries roughly 250 Thai recordings plus 289 Lingua Libre files -- too
few to be worth a fetcher, and the wiktionary audio kind is out of scope this
session anyway.

Cache stems are namespaced (``googletts_th`` / ``sentencetts_th``) because the
caches key on term and reading only.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config import AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults

_PAIYANNOI = "\N{THAI CHARACTER PAIYANNOI}"


def th_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """Ordered ``(term, reading)`` pairs for the audio retry ladder.

    Thai has no inflection, so there is one spelling -- except for the
    abbreviation mark: an audio pack may file the abbreviated headword without
    it. Both carry the same Paiboon reading, so a hit on either is the right
    file.
    """
    term = getattr(word, "mined_form", "") or ""
    reading = getattr(word, "expression_reading", "") or ""
    if not term:
        return []
    pairs = [(term, reading)]
    stripped = term.rstrip(_PAIYANNOI)
    if stripped and stripped != term:
        pairs.append((stripped, reading))
    return pairs


def th_speakable(term: str, reading: str) -> str | None:
    """What a synthetic voice speaks for a Thai pair: the Thai script.

    The reading slot holds Paiboon transcription, which a th-TH voice would read
    letter by letter. Thai orthography is close to phonemic, so the term is what
    the voice can pronounce.
    """
    return term or None


TH_AUDIO = AudioDefaults(
    gtts_lang="th",
    cache_stem_prefix="googletts_th",
    sentence_cache_stem_prefix="sentencetts_th",
    custom_fetcher_language="th",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=th_audio_candidates,
    speakable=th_speakable,
)
