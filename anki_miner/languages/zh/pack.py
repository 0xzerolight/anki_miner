"""Dependency-pack manifest for Chinese mining.

Pins jieba, pypinyin and opencc at versions ``pyproject.toml``'s ``[zh]`` extra
accepts. The extra pins floors rather than exact versions, so a pip user may be
running newer ones; the exact bytes are pinned here and in ``requirements.lock``.

opencc stays optional (mirrors ``ZH_OPTIONAL_PACKAGES``) because Chinese still
mines without it: a simplified corpus never reaches a converter. Traditional
input does — the tokenizer cuts a simplified copy of every line
(``zh/tokenizer.py``) and readings come off that copy (``zh/reading.py``) — so
it is pinned for every CPython the project supports rather than for the bundle's
alone, and only a platform with no wheel at all degrades.
"""

from __future__ import annotations

from collections.abc import Mapping

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

#: clib/ ships prebuilt CLI binaries/headers this package never imports; the
#: ~2 MB library payload stays (no functional proof yet that trimming it is safe).
_OPENCC_EXCLUDE = ("clib/bin/", "clib/include/")


def _opencc(abi: tuple[int, int], wheels: Mapping[tuple[str, str], tuple[str, str]]) -> PackComponent:
    """One opencc component for CPython *abi*, from ``{platform: (url, sha256)}``.

    opencc publishes a wheel per CPython and ``PackComponent`` carries one ABI,
    so the pack declares a sibling per supported interpreter instead of pinning
    the bundle's. They are alternatives, never additions: ``artifact_for``
    resolves nothing for a sibling whose ABI is not this interpreter's, and all
    four extract to the same ``opencc/`` directory, so one install downloads one
    wheel and the download figure below counts one.
    """
    return PackComponent(
        import_name="opencc",
        required=False,
        sentinels=("__init__.py",),
        abi=abi,
        per_platform={
            platform: ArtifactSpec(
                url=url,
                sha256=sha256,
                kind="wheel",
                member_prefix="opencc/",
                exclude=_OPENCC_EXCLUDE,
            )
            for platform, (url, sha256) in wheels.items()
        },
    )


_OPENCC_311 = _opencc(
    (3, 11),
    {
        ("linux", "x86_64"): (
            "https://files.pythonhosted.org/packages/70/eb/637c7cb0e79f8109709563d4435192641f95393ee423166a324b306f971b/opencc-1.4.2-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
            "794afd1f5254b22fb7e8655d3e60f0467e4a1ad5ebbd62f9efdd2dd421dd43db",
        ),
        ("linux", "aarch64"): (
            "https://files.pythonhosted.org/packages/dc/a0/3b9eed66849db84d98502522e427718ac4c97368047055a84b911f17204c/opencc-1.4.2-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
            "689eea00ddfdaa3ba0b78426da34759dcb07db52b2fc1c660fa62b3d94ee4f04",
        ),
        ("win32", "AMD64"): (
            "https://files.pythonhosted.org/packages/c2/38/f3d6db1ba92e1d042f89babce2ef554bb9728db5a42120c4f37e7d590fcb/opencc-1.4.2-cp311-cp311-win_amd64.whl",
            "0c0a14242f9b0a9932eccf4dd50fc5e26133940246a00862f60971fff3e78b02",
        ),
        ("darwin", "arm64"): (
            "https://files.pythonhosted.org/packages/5b/b7/d78cff32ed0820985aa7148a43bf96f1e1927a8ca3e7988f465065a052fe/opencc-1.4.2-cp311-cp311-macosx_11_0_arm64.whl",
            "a3bb8d817b8d5500fda9a81e245825d176b087e4d31702dafc2ef83d6ef21b4a",
        ),
        ("darwin", "x86_64"): (
            "https://files.pythonhosted.org/packages/04/74/673131660e3c4cf9cb8540bde081227a28ea686b22f7a0b64fa4150456a6/opencc-1.4.2-cp311-cp311-macosx_10_9_x86_64.whl",
            "5c5a76365426fb1c346c00a1729000dfc92ee621669c0b12ba33c008a6f2c300",
        ),
    },
)

_OPENCC_312 = _opencc(
    (3, 12),
    {
        ("linux", "x86_64"): (
            "https://files.pythonhosted.org/packages/75/8f/e8b80f225440a045c08dfc9bf251c8cb1019e0935d104c56715afff468ad/opencc-1.4.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
            "d90a8b76ea5d1f425a4f2eb16114cb33abd29a73c6a4ec367361c61b1c059a10",
        ),
        ("linux", "aarch64"): (
            "https://files.pythonhosted.org/packages/58/c2/6d6de602d5800b897a92eb6aad9c901eed562912dac3a0d099a06572710d/opencc-1.4.2-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
            "eb5df4bd9bd766aaa533d961123b519b51d6b678437b89650e8ffe9564c682c7",
        ),
        ("win32", "AMD64"): (
            "https://files.pythonhosted.org/packages/43/83/ed548fd759ee4dfdd88a1f57f6877b5fd421f582e66bdf01979771d6cbba/opencc-1.4.2-cp312-cp312-win_amd64.whl",
            "7025dc276b2a60b30ed3aefb99f1ceb8616076fd3eb3310c0f8f2046e79e76b1",
        ),
        ("darwin", "arm64"): (
            "https://files.pythonhosted.org/packages/f3/44/03bf0db03120e10f18fa1f3453593d5540b6c4250e5dc15b9551f8ffe976/opencc-1.4.2-cp312-cp312-macosx_11_0_arm64.whl",
            "2992898ccfb14aaa9feef5a38e08c4c20a79f41482626fc5a6e8ee87b23016e2",
        ),
        ("darwin", "x86_64"): (
            "https://files.pythonhosted.org/packages/4c/ad/9926e816dd654239905c4bc45997752dbe2d3d113a75cf77ba8ed866271c/opencc-1.4.2-cp312-cp312-macosx_10_13_x86_64.whl",
            "052177a890ac2fdd960402d5a482163c965ec68ba7511271c19db327a5606616",
        ),
    },
)

_OPENCC_313 = _opencc(
    (3, 13),
    {
        ("linux", "x86_64"): (
            "https://files.pythonhosted.org/packages/23/f2/80e1bfdb82b16057c9961b889d15f644b0b0ea500c2ba6d1845192c23ee9/opencc-1.4.2-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
            "25c34e75fe2ab2bf5b43d89e2cf9413fbfd693c9e4851c9b7b641d50e9793f63",
        ),
        ("linux", "aarch64"): (
            "https://files.pythonhosted.org/packages/fe/32/aa83c2631bb1d829e1782505da9ce9511bb110fb22bb9e9fc72de10e8a6e/opencc-1.4.2-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
            "ba6d6f45ce4908e0c24a034d844d3f43be6f2e8c69a838028806f1af8e721bef",
        ),
        ("win32", "AMD64"): (
            "https://files.pythonhosted.org/packages/cc/15/7bc47cc20436d1eb7163f15b200531bb4e18a6ee261784465019f87b049e/opencc-1.4.2-cp313-cp313-win_amd64.whl",
            "4338dc5c7c6c7b42a847f3a8ecbbdfb2543e39d63bebcbe25f10e3c81b1c76fb",
        ),
        ("darwin", "arm64"): (
            "https://files.pythonhosted.org/packages/8e/88/9e8cd33abb5ef4d57391088a7a74d0f7b3c7143d9c0abeda99be7849f814/opencc-1.4.2-cp313-cp313-macosx_11_0_arm64.whl",
            "01674abf96cd6b6358755d692f1d56fde39deb77383a095c23c211cc8502e57f",
        ),
        ("darwin", "x86_64"): (
            "https://files.pythonhosted.org/packages/34/b9/5e31c48d97a4738aa5c6ceb3f7c27693f74354d1603103eaad543b7660b8/opencc-1.4.2-cp313-cp313-macosx_10_13_x86_64.whl",
            "0e444f4bf4aff9f7396289652ea80c456c7b0237a8431cd0aece350d314e8816",
        ),
    },
)

_OPENCC_314 = _opencc(
    (3, 14),
    {
        ("linux", "x86_64"): (
            "https://files.pythonhosted.org/packages/c1/f4/9402bf733bd685b6a54a86d4f6ba3893e03088e0816080b5b95b63a94fda/opencc-1.4.2-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
            "d054897f2e597d663410b9dc20b9e79d9410a893b9578ef3eaf3f6eef5fbcb52",
        ),
        ("linux", "aarch64"): (
            "https://files.pythonhosted.org/packages/03/a6/45a09a6f0344cca2d645254ee9494684733ecdf1c7faf3135577430a4d8c/opencc-1.4.2-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
            "050bdc8516b4830be810504dfed1e5d6ac8ca81f19030b7c187abec5160683ca",
        ),
        ("win32", "AMD64"): (
            "https://files.pythonhosted.org/packages/92/8b/60963db0f968623ce7ce002f7480c083ebf8732314efc03c4f87a2d8f1d5/opencc-1.4.2-cp314-cp314-win_amd64.whl",
            "06f215590050d8504ceb713be0c822dd00b14095fd10a0b66f689bab40821119",
        ),
        ("darwin", "arm64"): (
            "https://files.pythonhosted.org/packages/19/0b/94de5296f99aa3a7e8e9ced388b0ada742b57394eb8cec8bc5ecc882af54/opencc-1.4.2-cp314-cp314-macosx_11_0_arm64.whl",
            "2dd2f8c3f7e633d252753c8f69298d5f446e02d62a3cf9c6a3c18683b5346c89",
        ),
        ("darwin", "x86_64"): (
            "https://files.pythonhosted.org/packages/fd/66/6198bde333ecc6151d7ee3e25c6d5ba2b9494130c1739e943e03ad459830/opencc-1.4.2-cp314-cp314-macosx_10_15_x86_64.whl",
            "0b14d64943de6c3575ae1e863dbbf4a3c6c7e7ce7e90f85772b2ec4dc24a5aca",
        ),
    },
)

PACK = LanguagePack(
    code="zh",
    # jieba's sdist, pypinyin's wheel and ONE opencc wheel: the four opencc
    # siblings are one interpreter's alternatives, not four downloads.
    approx_download_mb=23,
    components=(_JIEBA, _PYPINYIN, _OPENCC_311, _OPENCC_312, _OPENCC_313, _OPENCC_314),
)

__all__ = ["PACK"]
