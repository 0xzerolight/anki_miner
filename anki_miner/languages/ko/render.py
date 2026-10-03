"""Korean-only card fields, merged into extra_fields by _phase5_create.

The hook reads the mined spelling with getattr(word, "mined_form", ""):
EpisodeProcessor._apply_render_hooks passes the TokenizedWord itself, and any
AttributeError raised here is swallowed by that loop's except - the field would
just never appear on the card, with only a warning in the log.

One hook, not two: the planned NIKL vocabulary-grade field is void, because the
learner-grade list it would read is KOGL Type 4 and permits no derivative.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from anki_miner.languages.ko.script import is_hanja, krdict_headword
from anki_miner.languages.profile import CARD_FRONT_KEY

if TYPE_CHECKING:  # annotation-only: the Protocol and the config are types here, never runtime values
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook


def _mined(word: Any) -> str:
    """The card front for *word*; "" when the object carries none."""
    return str(getattr(word, "mined_form", "") or "")


class KoHanjaHook:
    """Hanja written in the mined form itself (kiwi's SH tag territory).

    v1 scope, deliberately: dictionary-sourced hanja for a pure-hangul headword
    (학생 -> 學生) needs structured KRDICT entry data the provider chain does not
    expose, and spec 16 defers it. Mixed-script text - the case that actually
    appears in subtitles and older prose - is covered here.

    A word written in hanja only (學校) is the one case that also moves the
    front: KRDICT's hanja-keyed row bolds the hangul headword, which goes out
    as CARD_FRONT_KEY (학교 on the front, 學校 in the Hanja field). With no
    such headword the front stays the hanja and the Hanja field stays empty,
    since it would only repeat the front. mined_form is untouched either way,
    so lookups and the known-words key are still the hanja.

    The han ranges are languages/ko/script.py's, shared with the known-word
    ingestion gate, so the two can never disagree about what a hanja is.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("hanja",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the hanja run is always emitted; no setting gates it
        mined = _mined(word)
        hanja = "".join(ch for ch in mined if is_hanja(ch))
        if not hanja or hanja != mined:
            return {"hanja": hanja}
        hangul = krdict_headword(str(getattr(word, "definition_html", "") or ""))
        if not hangul:
            return {}
        return {"hanja": hanja, CARD_FRONT_KEY: hangul}


KO_RENDER_HOOKS: tuple[CardRenderHook, ...] = (KoHanjaHook(),)
