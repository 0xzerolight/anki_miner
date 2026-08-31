"""Dependency-pack manifest for Chinese mining.

Pins the same jieba/pypinyin/opencc versions ``pyproject.toml``'s ``[zh]``
extra installs, so a pack user and a pip user run byte-identical engines.
opencc stays optional (mirrors ``ZH_OPTIONAL_PACKAGES``): it only adds
simplified/traditional fallbacks, so a platform this pack has no wheel for
still mines Chinese.
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent

_JIEBA = PackComponent(
    import_name="jieba",
    required=True,
    sentinels=("__init__.py", "dict.txt"),
    universal=ArtifactSpec(
        # jieba-0.42.1.tar.gz
        url=(
            "https://files.pythonhosted.org/packages/c6/cb/"
            "18eeb235f833b726522d7ebed54f2278ce28ba9438e3135ab0278d9792a2/"
            "jieba-0.42.1.tar.gz"
        ),
        sha256="055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2",
        kind="sdist",
        member_prefix="jieba-0.42.1/jieba/",
        # The .p pickles are Jython-only fallbacks; CPython loads the .py
        # tables beside them. lac_small/ is an unrelated bundled model.
        # analyse/idf.txt goes too: jieba.analyse builds a TFIDF object at
        # import and opens idf.txt doing it, so `import jieba.analyse` raises
        # from a pack. Nothing in anki_miner imports it (only jieba.posseg).
        exclude=(
            "lac_small/",
            "analyse/idf.txt",
            "posseg/char_state_tab.p",
            "posseg/prob_emit.p",
            "posseg/prob_start.p",
            "posseg/prob_trans.p",
            "finalseg/prob_emit.p",
            "finalseg/prob_start.p",
            "finalseg/prob_trans.p",
        ),
    ),
)

_PYPINYIN = PackComponent(
    import_name="pypinyin",
    required=True,
    sentinels=("__init__.py",),
    universal=ArtifactSpec(
        # pypinyin-0.55.0-py2.py3-none-any.whl
        url=(
            "https://files.pythonhosted.org/packages/b9/7b/"
            "4cabc76fcc21c3c7d5c671d8783984d30ac9d3bb387c4ba784fca3cdfa3a/"
            "pypinyin-0.55.0-py2.py3-none-any.whl"
        ),
        sha256="d53b1e8ad2cdb815fb2cb604ed3123372f5a28c6f447571244aca36fc62a286f",
        kind="wheel",
        member_prefix="pypinyin/",
    ),
)

_OPENCC = PackComponent(
    import_name="opencc",
    required=False,
    sentinels=("__init__.py",),
    abi=(3, 12),
    per_platform={
        ("linux", "x86_64"): ArtifactSpec(
            # opencc-1.4.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
            url=(
                "https://files.pythonhosted.org/packages/75/8f/"
                "e8b80f225440a045c08dfc9bf251c8cb1019e0935d104c56715afff468ad/"
                "opencc-1.4.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
            ),
            sha256="d90a8b76ea5d1f425a4f2eb16114cb33abd29a73c6a4ec367361c61b1c059a10",
            kind="wheel",
            member_prefix="opencc/",
            # clib/ ships prebuilt CLI binaries/headers this package never
            # imports; the ~2 MB library payload stays (no functional proof
            # yet that trimming it is safe).
            exclude=("clib/bin/", "clib/include/"),
        ),
        ("linux", "aarch64"): ArtifactSpec(
            # opencc-1.4.2-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
            url=(
                "https://files.pythonhosted.org/packages/58/c2/"
                "6d6de602d5800b897a92eb6aad9c901eed562912dac3a0d099a06572710d/"
                "opencc-1.4.2-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"
            ),
            sha256="eb5df4bd9bd766aaa533d961123b519b51d6b678437b89650e8ffe9564c682c7",
            kind="wheel",
            member_prefix="opencc/",
            exclude=("clib/bin/", "clib/include/"),
        ),
        ("win32", "AMD64"): ArtifactSpec(
            # opencc-1.4.2-cp312-cp312-win_amd64.whl
            url=(
                "https://files.pythonhosted.org/packages/43/83/"
                "ed548fd759ee4dfdd88a1f57f6877b5fd421f582e66bdf01979771d6cbba/"
                "opencc-1.4.2-cp312-cp312-win_amd64.whl"
            ),
            sha256="7025dc276b2a60b30ed3aefb99f1ceb8616076fd3eb3310c0f8f2046e79e76b1",
            kind="wheel",
            member_prefix="opencc/",
            exclude=("clib/bin/", "clib/include/"),
        ),
        ("darwin", "arm64"): ArtifactSpec(
            # opencc-1.4.2-cp312-cp312-macosx_11_0_arm64.whl
            url=(
                "https://files.pythonhosted.org/packages/f3/44/"
                "03bf0db03120e10f18fa1f3453593d5540b6c4250e5dc15b9551f8ffe976/"
                "opencc-1.4.2-cp312-cp312-macosx_11_0_arm64.whl"
            ),
            sha256="2992898ccfb14aaa9feef5a38e08c4c20a79f41482626fc5a6e8ee87b23016e2",
            kind="wheel",
            member_prefix="opencc/",
            exclude=("clib/bin/", "clib/include/"),
        ),
        ("darwin", "x86_64"): ArtifactSpec(
            # opencc-1.4.2-cp312-cp312-macosx_10_13_x86_64.whl
            url=(
                "https://files.pythonhosted.org/packages/4c/ad/"
                "9926e816dd654239905c4bc45997752dbe2d3d113a75cf77ba8ed866271c/"
                "opencc-1.4.2-cp312-cp312-macosx_10_13_x86_64.whl"
            ),
            sha256="052177a890ac2fdd960402d5a482163c965ec68ba7511271c19db327a5606616",
            kind="wheel",
            member_prefix="opencc/",
            exclude=("clib/bin/", "clib/include/"),
        ),
    },
)

PACK = LanguagePack(
    code="zh",
    approx_download_mb=23,
    components=(_JIEBA, _PYPINYIN, _OPENCC),
)

__all__ = ["PACK"]
