"""CC-CEDICT glosses as MarvNC's Yomitan packs ship them (zh CC-CEDICT, yue CC-CEDICT.Canto)."""

from __future__ import annotations

from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry


def cc_cedict_gloss(*items: list) -> str:
    """A gloss as MarvNC's CC-CEDICT pack ships it, rendered the way a card gets it.

    Each item is one gloss line's content. :func:`classifier` builds the pack's
    ``CL:`` label and its classifier links: per-character ruby carrying the
    pinyin, a trad/simp pair joined by a full-width ｜. A trailing ``<style>``
    stands in for the block ``attach_card_style_block`` appends.
    """
    node = {"tag": "ul", "data": {"cccedict": "definition"}, "content": [{"tag": "li", "content": i} for i in items]}
    return (
        render_glossary_entry([{"type": "structured-content", "content": node}])
        + "<style>.c::after{content:'CL:'}</style>"
    )


def classifier(*groups: tuple[tuple[str, str], ...]) -> list:
    """``CL:`` and one reference per group; a group is ``((form, pinyin per char), ...)``, trad first."""
    label = {
        "tag": "span",
        "data": {"cccedict": "classifier"},
        "title": "Classifier / measure word",
        "content": {"tag": "span", "content": "CL:", "data": {"cccedict": "classifier-label"}},
    }
    out: list = [label]
    for i, group in enumerate(groups):
        links: list = []
        for j, (form, pinyin) in enumerate(group):
            if j:
                links.append("｜")
            rubies = [
                {
                    "tag": "ruby",
                    "content": [char, {"tag": "rt", "data": {"cccedict": "reference-reading"}, "content": p}],
                }
                for char, p in zip(form, pinyin.split(), strict=True)
            ]
            links.append({"tag": "a", "href": f"?query={form}", "content": rubies})
        if i:
            out.append(",")
        out.append({"tag": "span", "data": {"cccedict": "reference"}, "content": links})
    return out
