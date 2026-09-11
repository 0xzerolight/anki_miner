"""Manifest for the ASR engine pack (faster-whisper + CTranslate2 + PyAV).

Pins the exact versions ``requirements.lock`` resolves for the release build,
so a user who downloads the pack runs the same packages the bundle used to
carry. The frozen bundle EXCLUDES every package named here (``anki_miner.spec``)
and ``PyInstaller-Hooks/hook-faster_whisper.py`` is gone, so their absence from
a bare bundle is a guarantee — the pre-v2.10 lean ``.deb`` stripped package
dirs after the build, which left the pure-Python halves in the PYZ, made
``find_spec`` report the engine present, and offered a Download button that
could never work.

Component set = the Requires-Dist closure of faster-whisper that nothing else
in the bundle imports, filtered to what the ASR runtime path actually imports
(``import faster_whisper``, ``WhisperModel``, ``download_model``): ``yaml`` is
in because ``ctranslate2.converters`` imports it at package load;
``fsspec``/``typer``/``shellingham``/``annotated_doc``/``setuptools`` are
exclusive dependencies too but are never imported on that path, so they are
neither shipped nor excluded; ``hf_xet`` is exclusive AND excluded but NOT
shipped — huggingface_hub turns xet on only when ``importlib.metadata`` finds
the hf_xet dist-info, which neither this pack (it extracts the package tree
alone) nor the old bundle ever carried, so the wheel would be inert bytes.
``numpy``, ``tqdm``, ``certifi``, ``idna``, ``packaging``, ``rich``/``click``
stay in the bundle (core dependencies); the sub-modules the pack needs from
them that nothing in the base graph reaches (``tqdm.auto``,
``tqdm.contrib.concurrent``, stdlib ``asyncio``, ``secrets``) are pinned as
``hiddenimports`` in ``anki_miner.spec``.

Order matters: ``faster_whisper`` is LAST. The pack root joins ``sys.path`` as
soon as one component is complete, and ``_engine.available()`` needs both
``faster_whisper`` and ``ctranslate2``; installing the front package last means
a cancelled or crashed install can never look available.

Wheel layout decides the shape of each pin (read from every wheel's central
directory, see docs/superpowers/plans/2026-09-10-asr-pack.md §1): the Linux
ctranslate2 and the Linux/Windows av wheels carry an auditwheel/delvewheel
``<pkg>.libs/`` tree beside the package that the extension resolves by an
``$ORIGIN``-relative rpath, declared as a directory root member; every other
compiled component keeps its libraries inside the package dir. cp312 pins
(ctranslate2, yaml) mirror ``onnx_pack_installer._BUNDLE_PYTHON``; av and
tokenizers ship stable-ABI wheels (``abi=None``).
"""

from __future__ import annotations

from anki_miner.languages.pack_spec import ArtifactSpec, DependencyPack, PackComponent

_PYPI = "https://files.pythonhosted.org/packages/"

_LINUX = ("linux", "x86_64")
_WINDOWS = ("win32", "AMD64")
_MAC_ARM = ("darwin", "arm64")
_MAC_INTEL = ("darwin", "x86_64")


def _wheel(path: str, sha256: str, prefix: str, root_members: tuple[str, ...] = ()) -> ArtifactSpec:
    return ArtifactSpec(url=_PYPI + path, sha256=sha256, kind="wheel", member_prefix=prefix, root_members=root_members)


_CTRANSLATE2 = PackComponent(
    import_name="ctranslate2",
    required=True,
    sentinels=("__init__.py",),
    abi=(3, 12),
    per_platform={
        _LINUX: _wheel(
            "ea/34/a0ac6e2538b7d730e4537cd01ded7817dda9bc97f5b6161bbd52d16e70a3/"
            "ctranslate2-4.8.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
            "247efccc2da9a63e8bf22abb4e87789f44ec1454bdcb227b07860cdc826fc89a",
            "ctranslate2/",
            root_members=("ctranslate2.libs/",),
        ),
        _WINDOWS: _wheel(
            "2a/ed/2c3c7b110c48c36d024c5247195f2ad4fc1e34cbf482dab62ccb3898cb70/ctranslate2-4.8.0-cp312-cp312-win_amd64.whl",
            "06feaafe134aafa8cb2fb1fdb82e36f050bb05929dfde1a95f4fe4d7881dfc76",
            "ctranslate2/",
        ),
        _MAC_ARM: _wheel(
            "fd/f8/871b866c10d4fe4479866c4aa9c6a7ba4073dc2a657879d44411b2fb8f4c/ctranslate2-4.8.0-cp312-cp312-macosx_11_0_arm64.whl",
            "94ec37527dd815531209694854dd5177e763ed51d35b4b2c34da3c3ad2c9b9fd",
            "ctranslate2/",
        ),
        _MAC_INTEL: _wheel(
            "c4/ea/316e3df68e21f79e20c277bf5c65d9825a42484ed7e3df2e6e325275ea5f/ctranslate2-4.8.0-cp312-cp312-macosx_11_0_x86_64.whl",
            "f0b93d127a4efb6481e3d0da4c3a6ac9889a8e9d8b50f9930bc5b2401fe5e598",
            "ctranslate2/",
        ),
    },
)

_AV = PackComponent(
    import_name="av",
    required=True,
    sentinels=("__init__.py",),
    abi=None,
    per_platform={
        _LINUX: _wheel(
            "77/43/96b35170bf2e64e00a41748c6400ff73232dc0fc62ded283679fb07c7fe0/av-17.1.0-cp311-abi3-manylinux_2_28_x86_64.whl",
            "f9a65d1f48b818323fb411e80358f89d77dec340b01d27c6b2dfbb9cbf4b779f",
            "av/",
            root_members=("av.libs/",),
        ),
        _WINDOWS: _wheel(
            "6b/f2/53a7cd34adb6a971d7e6d99663e74db286966c9db8afdca17472fdf0f98e/av-17.1.0-cp311-abi3-win_amd64.whl",
            "5df5c1172ef1cf65a1529d612f7da7798ce2cf82c1ff7212466b538a6cc7214c",
            "av/",
            root_members=("av.libs/",),
        ),
        _MAC_ARM: _wheel(
            "6d/af/dfdf6fc7b17814b50d0aa9e7a7e37b87be91be3890f44b0d525433cd1fd1/av-17.1.0-cp311-abi3-macosx_14_0_arm64.whl",
            "43ebbe977f19a7f2d2bd1a4e119675a0b15e05852cf7309846b6ab922ba7ffe9",
            "av/",
        ),
        _MAC_INTEL: _wheel(
            "ec/87/8036b5c781bc3639ea04ef42d4e26da253bd4bd4311d8705b6a1c8824047/av-17.1.0-cp311-abi3-macosx_11_0_x86_64.whl",
            "ad7b4aa011093324b7118245f50ac6db244cfe9900d4072508a5245a2b0d3f41",
            "av/",
        ),
    },
)

_TOKENIZERS = PackComponent(
    import_name="tokenizers",
    required=True,
    sentinels=("__init__.py",),
    abi=None,
    per_platform={
        _LINUX: _wheel(
            "0d/d5/1353e5f677ec27c2494fb6a6725e82d56c985f53e90ec511369e7e4f02c6/"
            "tokenizers-0.23.1-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
            "5075b405006415ea148a992d093699c66eb01952bf59f4d5727089a98bda45a4",
            "tokenizers/",
        ),
        _WINDOWS: _wheel(
            "97/c9/2553f72aaf65a2797d4229e37fa7fbe38ffbf3e32912d31bdd78b3323e59/tokenizers-0.23.1-cp310-abi3-win_amd64.whl",
            "e7bfaf995c1bdbbd21d13539decb6650967013759318627d85daeb7881af16b7",
            "tokenizers/",
        ),
        _MAC_ARM: _wheel(
            "e2/6a/068ed9f6e444c9d7e9d55ce134181325700f3d7f30410721bdc8f848d727/tokenizers-0.23.1-cp310-abi3-macosx_11_0_arm64.whl",
            "e0948bbb1ac1d7cdfc9fb6d62c596e3b7550036ad60ecd654a66ad273326324e",
            "tokenizers/",
        ),
        _MAC_INTEL: _wheel(
            "87/39/b87a87d5bb9470610b80a2d31df42fcffeaf35118b8b97952b2aff598cc7/tokenizers-0.23.1-cp310-abi3-macosx_10_12_x86_64.whl",
            "e03d6ffcbe0d56ee9c1ccd070e70a13fa750727c0277e138152acbc0252c2224",
            "tokenizers/",
        ),
    },
)

_YAML = PackComponent(
    import_name="yaml",
    required=True,
    sentinels=("__init__.py",),
    abi=(3, 12),
    per_platform={
        _LINUX: _wheel(
            "8b/9d/b3589d3877982d4f2329302ef98a8026e7f4443c765c46cfecc8858c6b4b/"
            "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
            "ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc",
            "yaml/",
        ),
        _WINDOWS: _wheel(
            "86/bf/899e81e4cce32febab4fb42bb97dcdf66bc135272882d1987881a4b519e9/pyyaml-6.0.3-cp312-cp312-win_amd64.whl",
            "5fcd34e47f6e0b794d17de1b4ff496c00986e1c83f7ab2fb8fcfe9616ff7477b",
            "yaml/",
        ),
        _MAC_ARM: _wheel(
            "89/a0/6cf41a19a1f2f3feab0e9c0b74134aa2ce6849093d5517a0c550fe37a648/pyyaml-6.0.3-cp312-cp312-macosx_11_0_arm64.whl",
            "fc09d0aa354569bc501d4e787133afc08552722d3ab34836a80547331bb5d4a0",
            "yaml/",
        ),
        _MAC_INTEL: _wheel(
            "d1/33/422b98d2195232ca1826284a76852ad5a86fe23e31b009c9886b2d0fb8b2/pyyaml-6.0.3-cp312-cp312-macosx_10_13_x86_64.whl",
            "7f047e29dcae44602496db43be01ad42fc6f1cc0d8cd6c83d342306c32270196",
            "yaml/",
        ),
    },
)


def _pure(name: str, path: str, sha256: str, *sentinels: str) -> PackComponent:
    return PackComponent(
        import_name=name,
        required=True,
        sentinels=("__init__.py", *sentinels),
        universal=_wheel(path, sha256, f"{name}/"),
    )


_HUGGINGFACE_HUB = _pure(
    "huggingface_hub",
    "49/79/621a7dbb80c70974f73a597275351ebe03ce5bc65cb5f8f4acb5859252bc/huggingface_hub-1.16.1-py3-none-any.whl",
    "64340de934b9ce37857ef85a82de72f5629e8a270f9119eabb12bf495eb53c22",
    "file_download.py",
)
_FILELOCK = _pure(
    "filelock",
    "13/37/a065dc3bd6e49423a6532c642ca7378d3f467b1ef44c2800c937af7f9739/filelock-3.29.4-py3-none-any.whl",
    "dac1648087d5115554850d113e7dd8c83ab2d38e3435dde2d4f163847e57b767",
)
_HTTPX = _pure(
    "httpx",
    "2a/39/e50c7c3a983047577ee07d2a9e53faf5a69493943ec3f6a384bdc792deb2/httpx-0.28.1-py3-none-any.whl",
    "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad",
)
_HTTPCORE = _pure(
    "httpcore",
    "7e/f5/f66802a942d491edb555dd61e3a9961140fd64c90bce1eafd741609d334d/httpcore-1.0.9-py3-none-any.whl",
    "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55",
)
_H11 = _pure(
    "h11",
    "04/4b/29cac41a4d98d144bf5f6d33995617b185d14b22401f75ca86f384e87ff1/h11-0.16.0-py3-none-any.whl",
    "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86",
)
_ANYIO = _pure(
    "anyio",
    "ba/16/9826f089383c593cdfc4a6e5aca94d9e91ae1692c57af82c3b2aa5e810f7/anyio-4.14.0-py3-none-any.whl",
    "dd9b7a2a9799ed6552fde617b2c5df02b7fdd7d88392fc48101e51bae46164d9",
)
_FASTER_WHISPER = _pure(
    "faster_whisper",
    "05/99/49ee85903dee060d9f08297b4a342e5e0bcfca2f027a07b4ee0a38ab13f9/faster_whisper-1.2.1-py3-none-any.whl",
    "79a66ad50688c0b794dd501dc340a736992a6342f7f95e5811be60b5224a26a7",
    "transcribe.py",
)

PACK = DependencyPack(
    name="asr",
    # The Linux total (the largest leg); Windows is ~56 MB, macOS ~30-45 MB.
    approx_download_mb=90,
    components=(
        _CTRANSLATE2,
        _AV,
        _TOKENIZERS,
        _YAML,
        _HUGGINGFACE_HUB,
        _FILELOCK,
        _HTTPX,
        _HTTPCORE,
        _H11,
        _ANYIO,
        _FASTER_WHISPER,
    ),
)

__all__ = ["PACK"]
