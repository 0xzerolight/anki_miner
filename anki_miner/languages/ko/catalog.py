"""Recommended downloadable ko resources (spec 10.1).

Same contract as ``services/resource_catalog.RECOMMENDED_DEFAULT_SET``: ``id``
is the PINNED on-disk slot the importer writes to, and every ``url`` is fed
straight to ``resource_downloader.download_to_temp`` and routed by ``kind``.

**Dictionary — KRDICT (한국어기초사전), 국립국어원.** CC BY-SA 2.0 KR, which is
the licence 국립국어원 has distributed 한국어기초사전, 표준국어대사전 and 우리말샘
under since 2019-03-11. The pinned artifact states it itself: every build's
``index.json`` carries ``"attribution": "세계인이 누리는 한국어 학습사전 by
국립국어원 is licensed under CC BY-SA 2.0 KR"`` plus the policy URL, so the
licence travels with the file rather than depending on the GitHub repo hosting
the Yomitan conversion (which has no LICENSE file — its absence says nothing
about the data, and share-alike leaves the converter no other option anyway).
Attribution therefore runs to 국립국어원, the rights holder, not to the
converter; the ``license_note`` credits both in that order.

The **No.Examples** build is the one pinned, deliberately. 국립국어원 excludes
example sentences quoted from publications and newspapers from the open licence
— those are fair-use only — and that variant carries none, so its whole payload
sits inside CC BY-SA 2.0 KR. It is also a quarter of the size.

**Frequency — NIKL 현대 국어 사용 빈도 조사 2 (2005), 김한샘, 국립국어원.** KOGL
Type 1 (출처표시), which permits derivatives and commercial use as long as
국립국어원 is credited. Not listed here because it has the licence and the wrong
shape: 국립국어원 publishes it as one archive of flat TSV and spreadsheet
members, not as a Yomitan ``frequency`` meta-bank, so
``services/frequency/source_importer.py`` rejects it as downloaded; its endpoint
also requires a matching ``Referer`` and refuses ranged requests, which the
downloader never sends. It stays a documented manual import — the same route
``zh/catalog.py`` records for SUBTLEX-CH. Convert the archive's
``일반어휘통계.txt`` member with ``scripts/convert_nikl_frequency.py`` — it writes
a direction-declared CSV of roughly 73,800 rows — then import that CSV like any
other frequency source through Settings.

Any other Yomitan-format Korean dictionary the user already has imports
unchanged through the same Settings flow; the app recommends none by name beyond
the one above.

The learner-vocabulary list from the same institute is not usable here at all:
it is KOGL Type 4 (변경금지), so no derivative of it may ship.
"""

from __future__ import annotations

from anki_miner.languages.profile import ResourceSpec

KO_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="krdict-en",
        kind="dict",
        display_name="KRDICT (Korean-English)",
        url="https://github.com/Lyroxide/yomitan-ko-dic/releases/latest/download/KO-EN.KRDICT.No.Examples.zip",
        license_note=(
            "KRDICT — 한국어기초사전 by 국립국어원, CC BY-SA 2.0 KR; "
            "Yomitan build by Lyroxide, downloaded from upstream source."
        ),
    ),
)
