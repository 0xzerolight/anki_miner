"""Recommended downloadable ko resources (spec 10.1) — deliberately empty.

Same contract as ``services/resource_catalog.RECOMMENDED_DEFAULT_SET``: ``id``
is the PINNED on-disk slot the importer writes to, and every ``url`` is fed
straight to ``resource_downloader.download_to_temp`` and routed by ``kind``.

Nothing is listed, because a spec must be both licensed for redistribution by
link and shaped like the importer it is routed to, and no Korean resource is
currently both. The frequency survey below has the licence and the wrong shape;
the community Yomitan ports of the Korean dictionaries have the shape and state
no licence. Both stay documented manual imports — the same route
``zh/catalog.py`` records for SUBTLEX-CH — so the app bundles nothing and
imports every ko resource through the same Settings flow the JA dictionaries
use.

**Frequency — NIKL 현대 국어 사용 빈도 조사 2 (2005), 김한샘, 국립국어원.** KOGL
Type 1 (출처표시), which permits derivatives and commercial use as long as
국립국어원 is credited. Published by 국립국어원 as one archive of flat TSV and
spreadsheet members, not as a Yomitan ``frequency`` meta-bank, so
``services/frequency/source_importer.py`` rejects it as downloaded; its endpoint
also requires a matching ``Referer`` and refuses ranged requests, which the
downloader never sends. Convert the archive's ``일반어휘통계.txt`` member with
``scripts/convert_nikl_frequency.py`` — it writes a direction-declared CSV of
roughly 73,800 rows — then import that CSV like any other frequency source.

**Dictionary — whichever Yomitan-format Korean dictionary the user already
has.** It imports unchanged; the app recommends none by name until one states a
licence.

The learner-vocabulary list from the same institute is not usable here at all:
it is KOGL Type 4 (변경금지), so no derivative of it may ship.
"""

from __future__ import annotations

from anki_miner.languages.profile import ResourceSpec

KO_CATALOG: tuple[ResourceSpec, ...] = ()
