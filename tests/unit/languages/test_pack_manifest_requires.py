"""S18 governance: requirements resolve, and no import name is pinned twice."""

from __future__ import annotations

import importlib
from importlib.util import find_spec

from anki_miner.languages import AVAILABLE_LANGUAGES, SHARED_PACK_CODES


def _packs():
    out = {}
    for code in (*AVAILABLE_LANGUAGES, *SHARED_PACK_CODES):
        if find_spec(f"anki_miner.languages.{code}") is None:
            continue
        if find_spec(f"anki_miner.languages.{code}.pack") is not None:
            out[code] = importlib.import_module(f"anki_miner.languages.{code}.pack").PACK
    return out


def test_every_requirement_is_a_shared_pack_with_a_manifest():
    packs = _packs()
    for pack in packs.values():
        for requirement in pack.requires:
            assert requirement in SHARED_PACK_CODES
            assert requirement in packs


def test_no_import_name_is_pinned_at_two_urls():
    urls: dict[str, set[str]] = {}
    for pack in _packs().values():
        for comp in pack.components:
            specs = [comp.universal] if comp.universal else list((comp.per_platform or {}).values())
            urls.setdefault(comp.import_name, set()).update(spec.url.rsplit("/", 1)[-1].split("-")[1] for spec in specs)
    assert {name: versions for name, versions in urls.items() if len(versions) > 1} == {}
