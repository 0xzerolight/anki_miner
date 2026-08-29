"""Korean tokenizer: kiwipiepy behind the shared parse lock, emitting duck tokens.

build_tagger() is the name tagger_provider's generic non-ja branch resolves
(importlib.import_module("anki_miner.languages.ko.tokenizer").build_tagger()),
so Korean needs NO ko-specific code in tagger_provider and the built tagger is
stored in the process-wide {lang: tagger} cache rather than returned past it.
LockedTagger is reused verbatim from the ja stack (services/tagger.py:40): the
app's concurrent mining tabs hammer one instance, and one uncontended acquire
per subtitle line is the whole cost.

Tokens are anki_miner.languages.token.LanguageToken - the fugashi-shaped duck
token for NON-ja tokenizers. It is deliberately NOT services.morphology.
SyntheticToken: that class's isinstance gates (morphology.py:531, :581) drive
ja-only attested-reading and span-replacement passes a Korean token must never
enter.

surface is the VERBATIM SOURCE SLICE text[tok.start:tok.end], not tok.form:
kiwi restores irregular stems (걸어 -> 걷) and z_coda-splits colloquial codas, and
morphology.iter_token_spans locates each token with str.find from a running
cursor, dropping anything it cannot find. The dictionary form survives on
feature.lemma, which is what the mined-form policy reads.

POS is two-level, like zh's (coarse pos1, full flag in pos2) and like UniDic's
(名詞/固有名詞): pos1 is the two-letter Sejong class, pos2 the full base tag when
it differs. The shared gate tests pos2 against excluded_subtypes
(morphology.py:1071), so a flat one-level tag would make KO_EXCLUDED_SUBTYPES
dead config - see languages/ko/morphology.py.

Z_CODA tokens are dropped here. kiwi synthesises them from a colloquial coda
fused onto an ending (먹었어욥 -> ... + ᆸ/Z_CODA); the jamo does not stand alone in
the source line and its span overlaps the token before it, so it carries no
mineable content and would only desynchronise iter_token_spans' running cursor.
z_coda=True still matters: without it the whole colloquial form is swallowed
into one unanalysable NNG.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.util import find_spec
from typing import Any

from anki_miner.languages.ko.availability import KO_MODEL_DOWNLOAD_HINT
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

#: kiwi's own synthetic coda tag - never mined, never emitted (see docstring).
Z_CODA_TAG = "Z_CODA"


def resolve_model_path() -> str | None:
    """Return the Kiwi ``model_path``, or None to let Kiwi resolve it itself.

    The ladder, in order:

    1. The ``kiwipiepy_model`` PACKAGE. A pip install with the ``[ko]`` extra and
       every dev/CI env has it, and Kiwi's own native loader finds it — so the
       answer is None and nothing about those environments changes.
    2. The in-app download PACK (``services.ko_model_installer``). The frozen
       bundle deliberately excludes the ~88 MB model, so this is the path a
       bundled install takes once the user has downloaded it.
    3. Neither: raise ``ImportError`` naming the download. ``tagger_provider``
       chains it into the ``ValueError`` every caller already handles, so the
       reason reaches the user instead of a bare "No tokenizer registered".

    Resolved through ``find_spec`` and a couple of stat calls — nothing is
    imported or loaded here.
    """
    try:
        package_present = find_spec("kiwipiepy_model") is not None
    except (ImportError, ValueError):
        package_present = False
    if package_present:
        return None

    from anki_miner.services.ko_model_installer import is_installed, ko_model_path, ko_model_root

    root = ko_model_root()
    if is_installed(root):
        return str(ko_model_path(root))
    raise ImportError(f"The Korean language model is not installed. {KO_MODEL_DOWNLOAD_HINT}")


def _create_kiwi() -> Any:
    """Build a raw kiwipiepy.Kiwi against whichever model this install has.

    Imported function-locally: an install without the [ko] extra must fail here,
    with an actionable message, not at module import time.
    """
    from kiwipiepy import Kiwi

    model_path = resolve_model_path()
    if model_path is None:
        return Kiwi()
    return Kiwi(model_path=model_path)


def base_tag(tag: str) -> str:
    """Return the Sejong base tag, dropping kiwi's -R/-I regularity suffix."""
    return tag.split("-", 1)[0]


def coarse_tag(tag: str) -> str:
    """Return the two-letter Sejong class of *tag* (NNG -> NN, VV-I -> VV)."""
    return base_tag(tag)[:2]


def _source_slice(tok: Any, text: str) -> str:
    """Verbatim source substring for *tok*, falling back to its form."""
    start = getattr(tok, "start", None)
    end = getattr(tok, "end", None)
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        return text[start:end]
    return str(tok.form)


def to_duck_tokens(kiwi_tokens: Iterable[Any], text: str) -> list[LanguageToken]:
    """Convert kiwi Tokens to fugashi-shaped LanguageTokens."""
    out: list[LanguageToken] = []
    for tok in kiwi_tokens:
        raw_tag = str(getattr(tok, "tag", "") or "")
        base = base_tag(raw_tag)
        if base == Z_CODA_TAG:
            continue
        coarse = base[:2]
        lemma = str(getattr(tok, "lemma", "") or "") or str(tok.form)
        out.append(
            LanguageToken(
                surface=_source_slice(tok, text),
                pos1=coarse,
                pos2="" if base == coarse else base,
                lemma=lemma,
                kana="",
            )
        )
    return out


class KiwiTagger:
    """Callable with the fugashi tagger contract: tagger(text) -> tokens."""

    def __init__(self, kiwi: Any) -> None:
        self._kiwi = kiwi

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        return to_duck_tokens(self._kiwi.tokenize(text, z_coda=True), text)

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """Build the lock-guarded Korean tokenizer (tagger_provider's entry point)."""
    return LockedTagger(KiwiTagger(_create_kiwi()))
