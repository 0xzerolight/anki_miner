"""Convert already-mined cards to Yomitan's Anki-export shape (Restyle Mined Cards).

Cards mined before the export pass carry Anki Miner's internal glossary markup,
usually with a house ``<style>`` block: the minified base stylesheet on its
first line, then the card's carried dictionary CSS. The base sheet fought the
note type, so conversion drops it, keeps the carried dictionary CSS verbatim
(never re-collected: an uninstalled dictionary keeps its CSS) and rewrites the
body with ``anki_export.to_yomitan_export``, the pass new cards get. Every body
shape since v2.4.0 converts, including v2.4.0-v2.6.7 ``span.gloss-content``
bodies and v2.7.0 per-dictionary ``<style>`` blocks inside the wrapper; a field
the pass cannot fully convert, or would lose visible text from, is left as it
is. Converted and Yomitan-mined cards carry no internal markup, so a re-run
counts them as already up to date. Never writes note-type styling.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lxml import html as lxml_html  # type: ignore[import-untyped]

from anki_miner.languages.registry import config_language, get_profile
from anki_miner.services.definition_service import collect_dictionary_css_entries
from anki_miner.services.dictionary.anki_export import CONTROL_CHARS_RE, to_yomitan_export
from anki_miner.services.dictionary.dict_css_scope import attach_dict_css, is_internal_glossary

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.anki_service import AnkiService

logger = logging.getLogger(__name__)

# Anki search escaping for a note-type name (mirrors AnkiService._build_vocab_query):
# backslash, quote, and Anki's ``*``/``_`` glob metacharacters, so a name like
# ``Core_2k`` matches literally instead of as a wildcard.
_QUERY_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("*", "\\*"), ("_", "\\_"))

_CHUNK = 500

# The two house-block openings ever shipped. The base sheet always opened the
# block, newline-free, so everything after its first newline is the card's
# carried dictionary CSS.
_OWNED_STYLE_MARKERS = (
    ".yomitan-glossary{--anki-miner-owned-style: 1;",
    ".yomitan-glossary{--am-muted: rgba(128,128,128,0.95);",
)
# The rtl rule old blocks appended after the dictionary CSS: inert without
# ol[data-count], and the export pass now inlines its effect.
_LEGACY_RTL_CSS = '.yomitan-glossary ol[data-count] [data-sc-content="example-sentence-a"]{unicode-bidi:plaintext}'
_STYLE_SPAN_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
# Tag lines: the envelope's `<i>` and its tag chips move into the exported
# `<i>` label, so they are not content for the loss check.
_LABEL_XPATH = "//style | //li[@data-dictionary]/i | //li[@data-dictionary]/span[contains(@class, 'gloss-tag')]"


def _visible_chars(fragment: str) -> Counter[str]:
    """Non-whitespace text characters of a field, tag lines and stylesheets aside."""
    root = lxml_html.fragment_fromstring(CONTROL_CHARS_RE.sub("", fragment), create_parent="div")
    for el in root.xpath(_LABEL_XPATH):
        el.drop_tree()
    return Counter("".join(str(root.text_content()).split()))


def _convert_field(value: str, entries: list[tuple[str, str, str]], direction: str) -> str | None:
    """The field in export shape, or ``None`` to leave it untouched.

    ``None`` when the ``<style>`` structure is beyond repair (two house blocks,
    or a house marker outside a ``<style>``), when the converted field still
    holds internal markup (a body shape the pass does not know), or when it
    would lose visible text. Never strips the house block without converting the
    body, so a refused field keeps its old styling intact.
    """
    ours = [m for m in _STYLE_SPAN_RE.finditer(value) if m.group(1).startswith(_OWNED_STYLE_MARKERS)]
    if len(ours) > 1 or (not ours and any(marker in value for marker in _OWNED_STYLE_MARKERS)):
        return None
    if ours:
        match = ours[0]
        if _LEGACY_RTL_CSS in match.group(1):
            direction = "rtl"  # mined in an rtl language: keep its bidi, whatever is mined now
        body = value[: match.start()] + value[match.end() :]
        carried = match.group(1).partition("\n")[2].replace(_LEGACY_RTL_CSS, "").strip()
        if carried:
            body += f"<style>{carried}</style>"
    else:
        body = attach_dict_css(value, dict_css_entries=entries)
    converted = to_yomitan_export(body, direction=direction)
    if is_internal_glossary(converted) or _visible_chars(value) - _visible_chars(converted):
        return None
    return converted


@dataclass(frozen=True)
class RestyleResult:
    """Outcome of a conversion run. ``scanned`` counts notes examined.

    Aggregation is PER NOTE so the counters always sum to ``scanned``: a note is
    ``restyled`` if ANY carrier field was converted, else ``skipped_styled`` if
    any carrier field holds a glossary (already in Yomitan's shape, or left
    untouched because it could not be converted safely), else
    ``skipped_no_markup`` (non-glossary note). A changed note whose write was
    not confirmed is ``failed``.
    """

    scanned: int
    restyled: int
    skipped_styled: int
    skipped_no_markup: int
    failed: int = 0


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _escape_note_type(name: str) -> str:
    for old, new in _QUERY_ESCAPES:
        name = name.replace(old, new)
    return name


def restyle_mined_cards(
    anki_service: AnkiService,
    config: AnkiMinerConfig,
    *,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RestyleResult:
    """Convert the configured note type's mined cards to Yomitan's export shape.

    Both the glossary and the definition fields are converted when mapped,
    each independently (``_convert_field``); changed fields of one note are
    written in a single ``update_notes_fields`` entry. Idempotent: a converted
    card holds no internal markup, so a re-run counts it ``skipped_styled``.
    Never removes card content and never writes note-type styling.
    """
    glossary_field = config.anki_fields.get("glossary")
    definition_field = config.anki_fields.get("definition")
    # Ordered, deduped carrier list (both mapped to the SAME field name is a
    # degenerate config — process it once).
    carrier_fields = [f for f in (glossary_field, definition_field) if f]
    carrier_fields = list(dict.fromkeys(carrier_fields))
    if not carrier_fields:
        return RestyleResult(0, 0, 0, 0)

    entries = collect_dictionary_css_entries(config)
    direction = get_profile(config_language(config)).content_style.direction

    note_ids = anki_service.find_notes(f'note:"{_escape_note_type(config.anki_note_type)}"')
    scanned = restyled = skipped_styled = skipped_no_markup = failed = 0
    refused: list[int] = []

    for chunk in _chunks(note_ids, _CHUNK):
        if is_cancelled and is_cancelled():
            break
        updates: list[tuple[int, dict[str, str]]] = []
        for info in anki_service.notes_info(chunk):
            note_id = info.get("noteId")
            fields = info.get("fields")
            if not isinstance(note_id, int) or not isinstance(fields, dict):
                continue  # deleted ({}) / malformed
            scanned += 1
            changed: dict[str, str] = {}
            had_markup = False
            for field_name in carrier_fields:
                entry = fields.get(field_name)
                if not isinstance(entry, dict):
                    continue  # field absent on this note type instance
                value = entry.get("value", "") or ""
                if "yomitan-glossary" not in value:
                    continue
                had_markup = True
                if not is_internal_glossary(value):
                    continue  # already in Yomitan's shape
                new_value = _convert_field(value, entries, direction)
                if new_value is None:
                    refused.append(note_id)
                elif new_value != value:
                    changed[field_name] = new_value
            if changed:
                updates.append((note_id, changed))
            elif had_markup:
                skipped_styled += 1
            else:
                skipped_no_markup += 1
        if updates:
            successful_id_set = set(anki_service.update_notes_fields(updates))
            confirmed = sum(1 for note_id, _fields in updates if note_id in successful_id_set)
            restyled += confirmed
            failed += len(updates) - confirmed
        if progress:
            progress(scanned, len(note_ids))

    if refused:
        # Left with their old styling: a body shape or a hand edit the
        # conversion could not carry over without loss. Find them in Anki by id.
        logger.info("Restyle left %d field(s) unconverted, note ids: %s", len(refused), sorted(set(refused)))
    return RestyleResult(scanned, restyled, skipped_styled, skipped_no_markup, failed)
