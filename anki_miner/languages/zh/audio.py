"""zh expression- and sentence-audio defaults (spec 9.1 / 10.1).

No JPod101 equivalent ships in the default chain: the surveyed Chinese sources
are either paid or unstable, so the one default entry is the synthetic Google
Translate voice the app already carries for JA. A user adds an audio pack or a
custom_json server ahead of it exactly as on the JA side.

Cache stems are namespaced (``googletts_zh`` / ``sentencetts_zh``) because the
caches are keyed by term and reading only — a zh card for 我 and a ja card for
我 would otherwise share one file.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config import AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults
from anki_miner.languages.zh import variants


def zh_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """Ordered ``(term, reading)`` query pairs for the zh audio retry ladder.

    The ja ladder retries okurigana-only lemma variants; Chinese has no
    inflection, so the only alternate spelling worth a second request is the
    other script. Every variant carries the SAME pinyin reading — simplified and
    traditional differ in glyph, never in pronunciation — so a traditional pack
    hits on a simplified front and vice versa. Empty terms are dropped and
    duplicates collapse, so a single-script word issues exactly one request.
    """
    term = getattr(word, "mined_form", "") or ""
    reading = getattr(word, "expression_reading", "") or ""
    if not term:
        return []
    pairs: list[tuple[str, str]] = []
    for candidate in variants.variant_candidates(term):
        pair = (candidate, reading)
        if candidate and pair not in pairs:
            pairs.append(pair)
    return pairs


ZH_AUDIO = AudioDefaults(
    gtts_lang="zh-CN",
    cache_stem_prefix="googletts_zh",
    sentence_cache_stem_prefix="sentencetts_zh",
    custom_fetcher_language="zh",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=zh_audio_candidates,
)
