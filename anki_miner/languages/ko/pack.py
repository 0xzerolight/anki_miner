"""Dependency-pack manifest for Korean mining.

kiwipiepy ships an abi3 wheel (built once against the stable ABI, so one
per-platform pin covers every CPython minor version — ``abi=None``). Its
compiled core sits at the WHEEL ROOT, not inside ``kiwipiepy/``:
``kiwipiepy/_wrap.py`` does ``import _kiwipiepy``, so the ~41 MB
``_kiwipiepy.abi3.so`` (``.pyd`` on Windows) is declared as a ``root_members``
prefix and lands beside the package dir in the pack root.
kiwipiepy_model's pin is the same sdist bytes the retired ko model installer
downloaded into ``ko_model/``; keep them identical or a version bump re-fetches
the ~88 MB model for users who already have it.
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

_KIWIPIEPY = PackComponent(
    import_name="kiwipiepy",
    required=True,
    sentinels=("__init__.py",),
    abi=None,
    per_platform={
        ("linux", "x86_64"): ArtifactSpec(
            # kiwipiepy-0.23.2-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
            url=(
                "https://files.pythonhosted.org/packages/7f/0a/"
                "c3d4c9e4e43494ede5badd39ef157de33e46eb5668659afd80ef7c713dda/"
                "kiwipiepy-0.23.2-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
            ),
            sha256="46a0a9fd36727736e8010ff54c655639f5df1c2ec34b92679cd3a94e8734d81f",
            kind="wheel",
            member_prefix="kiwipiepy/",
            root_members=("_kiwipiepy.",),
        ),
        ("linux", "aarch64"): ArtifactSpec(
            # kiwipiepy-0.23.2-cp39-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
            url=(
                "https://files.pythonhosted.org/packages/81/c9/"
                "622cb3800e04b887d2fddfc05746e326f1864b4518fa3746d8887fefc219/"
                "kiwipiepy-0.23.2-cp39-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"
            ),
            sha256="c9cbe16ab16236935e8c596209ead8513c3fc54c162e7218fa06d484522812bc",
            kind="wheel",
            member_prefix="kiwipiepy/",
            root_members=("_kiwipiepy.",),
        ),
        ("win32", "AMD64"): ArtifactSpec(
            # kiwipiepy-0.23.2-cp39-abi3-win_amd64.whl
            url=(
                "https://files.pythonhosted.org/packages/c6/5d/"
                "a18b133599bed0adbf62878deff1e786f7edafefa8b843b269f1c7bd92ac/"
                "kiwipiepy-0.23.2-cp39-abi3-win_amd64.whl"
            ),
            sha256="2ee49d007b55955ffe4d91d56c9adaae6f358f8c6d5c281efbdf8162ecbad3e2",
            kind="wheel",
            member_prefix="kiwipiepy/",
            root_members=("_kiwipiepy.",),
        ),
        ("darwin", "arm64"): ArtifactSpec(
            # kiwipiepy-0.23.2-cp39-abi3-macosx_11_0_arm64.whl
            url=(
                "https://files.pythonhosted.org/packages/4b/dd/"
                "10dd6b63daf1c3e25ca72512d6c0ebbdac15911782f37ca283f10d3b753a/"
                "kiwipiepy-0.23.2-cp39-abi3-macosx_11_0_arm64.whl"
            ),
            sha256="8410195a640b1c3ec164e69f3249e2d7c9b3dcd2222f5a5a245eed1c27f1ca55",
            kind="wheel",
            member_prefix="kiwipiepy/",
            root_members=("_kiwipiepy.",),
        ),
        ("darwin", "x86_64"): ArtifactSpec(
            # kiwipiepy-0.23.2-cp39-abi3-macosx_10_14_x86_64.whl
            url=(
                "https://files.pythonhosted.org/packages/fe/d8/"
                "9d6bde6ad64e0b1d4d66fcc1a45c819b4afbf9d21763d7e1e427913dab13/"
                "kiwipiepy-0.23.2-cp39-abi3-macosx_10_14_x86_64.whl"
            ),
            sha256="951aa58836697f467d4436fefddb91ccb864673415f3dcd805c67f757100cb2e",
            kind="wheel",
            member_prefix="kiwipiepy/",
            root_members=("_kiwipiepy.",),
        ),
    },
)

_KIWIPIEPY_MODEL = PackComponent(
    import_name="kiwipiepy_model",
    required=True,
    # The files Kiwi() cannot start without; also what the legacy ``ko_model/``
    # tier is checked against.
    sentinels=("sj.morph", "default.dict", "combiningRule.txt"),
    universal=ArtifactSpec(
        # kiwipiepy_model-0.23.0.tar.gz
        url=(
            "https://files.pythonhosted.org/packages/77/59/"
            "28403890c5f757254bf2068ff321fb3e656fb2e5658a3de8bfc092e4fd83/"
            "kiwipiepy_model-0.23.0.tar.gz"
        ),
        sha256="498a22f5585e6c4a162423d7557eb3ee3f71cddc6e0aeb2650c50467e85933e2",
        kind="sdist",
        member_prefix="kiwipiepy_model-0.23.0/kiwipiepy_model/",
    ),
)

PACK = LanguagePack(
    code="ko",
    approx_download_mb=100,
    components=(_KIWIPIEPY, _KIWIPIEPY_MODEL),
)

__all__ = ["PACK"]
