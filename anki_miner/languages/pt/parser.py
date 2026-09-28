"""Portuguese SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``).

Enclisis is handled in the tokenizer (``pt/tokenizer.py``), where the model sees the split words. The
repair is for what ``pt_core_news_sm``'s lemmatizer leaves behind: it backs off to the surface
(``sinto``, ``tens``, ``maçãs``) or invents a lemma (``Diga`` -> ``digar``), and wty-pt-en's form-of
rows name the lemma. It reads the tagger's lemma before the surface (``surface_first=False``): over 77
ordinary subtitle cues that order fixed 24 of 38 wrong fronts and broke none, where surface-first broke
7 whose surface is a headword of its own (``vamos`` for ``ir``, ``pais`` for ``pai``, ``calças``).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass

    kwargs.setdefault("token_post_pass", FormOfLemmaPass(surface_first=False))
    return create_spaced_parser(config, **kwargs)
