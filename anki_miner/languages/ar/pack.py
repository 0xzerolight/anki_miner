"""Dependency-pack manifest for Arabic mining: the CAMeL Tools calima-msa-r13 morphology database.

Data only: the analyzer is the in-tree port in ``_calima/``. One plain zip from the CAMeL Lab's
``camel-tools-data`` GitHub release (``kind="zip"``, flat, extracted into ``calima_msa/``), pinned twice
(spec C.1): the zip exactly as GitHub serves it (the lab's own catalogue digest differs by 214 B) and the
``morphology.db`` inside it. GPL-2.0 data: the zip's own ``LICENSE`` lands beside the database and
``licenses/calima-msa-r13/`` ships with the app. Hand-written like ko/zh (``pin_language_pack.py`` resolves
PyPI and spaCy-model artifacts only); CI's ``test`` job re-verifies both digests on every run by seeding it.
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

_CALIMA_MSA = PackComponent(
    import_name="calima_msa",
    required=True,
    sentinels=("morphology.db", "LICENSE"),
    universal=ArtifactSpec(
        # morphology_db_calima-msa-r13-0.4.0.zip, 40,488,532 B; morphology.db inside is 40,471,399 B
        url=(
            "https://github.com/CAMeL-Lab/camel-tools-data/releases/download/2022.03.21/"
            "morphology_db_calima-msa-r13-0.4.0.zip"
        ),
        sha256="fe6531250c5529307627cc63ed56447cbb9968020d6ea3ab867e6ad9af94c738",
        kind="zip",
        member_prefix="",
        inner_sha256=(("morphology.db", "195bc25a333237a2126470da888d7936b59ed3729f9210e0a4194ba43497dd70"),),
    ),
)

PACK = LanguagePack(code="ar", approx_download_mb=41, components=(_CALIMA_MSA,))

__all__ = ["PACK"]
