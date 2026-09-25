"""Hebrew word audio (spec F.2): Google Translate under its legacy code, Edge as an option.

``gtts_lang`` is ``iw``, not ``he``. gTTS 2.5.4's table carries ``"iw": "Hebrew"`` and no ``he``
(probed), and it validates ``lang`` against that table, so ``he`` raises before any request leaves
-- even though the endpoint itself answers to both. The cache stems stay ``_he_`` because the app,
not gTTS, names the language.

The ladder offers the vocalised reading first: fed the points, the Google voice pronounces the
vowels a bare Hebrew spelling leaves out. A word the dictionary did not resolve has no reading, and
then the bare front is all there is to say.

``edge_voice`` is set but the default chain is Google's: Hebrew HAS a Google voice, so the Edge leg
is the user-addable alternative (Settings -> Word Audio -> Add audio source -> Online Source), not the
default. That is the seam's "user-added leg" shape, and the D14 contract test allows it.
"""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages.he.morphology import he_audio_candidates, he_speakable
from anki_miner.languages.profile import AudioDefaults

__all__ = ["HE_AUDIO"]

HE_AUDIO = AudioDefaults(
    gtts_lang="iw",
    cache_stem_prefix="googletts_he",
    sentence_cache_stem_prefix="sentencetts_he",
    custom_fetcher_language="he",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=he_audio_candidates,
    speakable=he_speakable,
    edge_voice="he-IL-HilaNeural",
)
