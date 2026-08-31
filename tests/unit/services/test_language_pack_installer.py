"""Tests for the generic language-pack installer.

Tests NEVER hit the network: ``download_to_temp`` is replaced by
:class:`_Downloader`, which serves ``.whl`` zips and ``.tar.gz`` sdists built in
memory, and every synthetic manifest pins the fixture's own sha256.

Most cases run against a SYNTHETIC pack whose import names (``xxpkg``,
``xxmodel``) can never be importable, because this venv has kiwipiepy, jieba and
friends installed — a real manifest would be satisfied by ``find_spec`` before
the disk tier is ever consulted, and the disk tier is what is under test.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import platform
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent
from anki_miner.services import language_pack_installer as installer
from tests.unit._resume_key_assert import assert_stable_resume_key as _assert_stable_resume_key

_THIS_PLATFORM = (sys.platform, platform.machine())
_OTHER_PLATFORM = ("noplat", "noarch")


# ----------------------------------------------------------------------
# Archive fixtures
# ----------------------------------------------------------------------


def _make_wheel(members: dict[str, bytes]) -> bytes:
    """Return zip bytes containing *members* (arcname -> contents)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


def _make_sdist(members: dict[str, bytes]) -> bytes:
    """Return gzipped tar bytes containing *members* (arcname -> contents)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for arcname, data in members.items():
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _spec(
    payload: bytes,
    *,
    kind: str,
    member_prefix: str,
    exclude: tuple[str, ...] = (),
    root_members: tuple[str, ...] = (),
) -> ArtifactSpec:
    """An ArtifactSpec pinned to *payload*'s own digest, with a unique url."""
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactSpec(
        # The digest is in the url so two fixtures sharing a member_prefix do not
        # overwrite each other in the downloader's payload table.
        url=f"https://example.invalid/{member_prefix.strip('/').replace('/', '-')}-{digest[:8]}.{kind}",
        sha256=digest,
        kind=kind,  # type: ignore[arg-type]
        member_prefix=member_prefix,
        exclude=exclude,
        root_members=root_members,
    )


_WHEEL_BYTES = _make_wheel(
    {
        "xxpkg/__init__.py": b"# package",
        "xxpkg/data.txt": b"payload",
        "xxpkg/sub/deep.txt": b"deep",
        "xxpkg/tests/heavy.bin": b"never extracted",
        # The near-miss pair jieba's manifest is made of: an excluded pickle and
        # the module a suffix away from it that the package actually imports.
        "xxpkg/table.p": b"never extracted",
        "xxpkg/table.py": b"# the real table",
        "xxpkg-1.0.dist-info/METADATA": b"never extracted",
    }
)
_WHEEL_SPEC = _spec(_WHEEL_BYTES, kind="wheel", member_prefix="xxpkg/", exclude=("tests/", "table.p"))

_SDIST_BYTES = _make_sdist(
    {
        "xxmodel-1.0/xxmodel/__init__.py": b"# model",
        "xxmodel-1.0/xxmodel/model.bin": b"weights",
        "xxmodel-1.0/xxmodel/notes/readme.txt": b"never extracted",
        "xxmodel-1.0/PKG-INFO": b"never extracted",
    }
)
_SDIST_SPEC = _spec(_SDIST_BYTES, kind="sdist", member_prefix="xxmodel-1.0/xxmodel/", exclude=("notes/",))

_WHEEL_COMPONENT = PackComponent(
    import_name="xxpkg",
    required=True,
    sentinels=("__init__.py", "data.txt"),
    universal=_WHEEL_SPEC,
)
_SDIST_COMPONENT = PackComponent(
    import_name="xxmodel",
    required=True,
    sentinels=("model.bin",),
    universal=_SDIST_SPEC,
)
_PACK = LanguagePack(code="xx", approx_download_mb=1, components=(_WHEEL_COMPONENT, _SDIST_COMPONENT))

# kiwipiepy's shape: the compiled extension sits at the WHEEL ROOT, beside the
# package dir, and ``import kiwipiepy`` dies without it.
_ROOT_WHEEL_BYTES = _make_wheel(
    {
        "xxroot/__init__.py": b"# package",
        "_xxroot.abi3.so": b"native",
        "xxroot-1.0.dist-info/METADATA": b"never extracted",
    }
)
_ROOT_WHEEL_SPEC = _spec(_ROOT_WHEEL_BYTES, kind="wheel", member_prefix="xxroot/", root_members=("_xxroot.",))
_ROOT_COMPONENT = PackComponent(
    import_name="xxroot",
    required=True,
    sentinels=("__init__.py",),
    universal=_ROOT_WHEEL_SPEC,
)
_ROOT_PACK = LanguagePack(code="xx", approx_download_mb=1, components=(_ROOT_COMPONENT,))


class _Downloader:
    """Stand-in for ``download_to_temp`` serving registered payloads from memory."""

    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}
        self.urls: list[str] = []
        self.resume_keys: list[str] = []
        self.after_download = None

    def register(self, *specs_and_payloads: tuple[ArtifactSpec, bytes]) -> None:
        for spec, payload in specs_and_payloads:
            self.payloads[spec.url] = payload

    def __call__(
        self,
        url,
        *,
        dest_dir,
        progress=None,
        cancelled_check=None,
        max_bytes=None,
        resume_key=None,
        resume_root=None,
    ) -> Path:
        _assert_stable_resume_key(resume_key)
        assert max_bytes == 200 * 1024 * 1024, f"artifact download is uncapped: {max_bytes}"
        self.urls.append(url)
        self.resume_keys.append(resume_key)
        payload = self.payloads[url]
        if progress is not None:
            progress(0, len(payload), "downloading")
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / f"artifact-{len(self.urls)}.part"
        part.write_bytes(payload)
        if self.after_download is not None:
            self.after_download()
        return part


@pytest.fixture
def downloader(monkeypatch) -> _Downloader:
    """Patched ``download_to_temp`` preloaded with the synthetic pack's artifacts."""
    fake = _Downloader()
    fake.register((_WHEEL_SPEC, _WHEEL_BYTES), (_SDIST_SPEC, _SDIST_BYTES), (_ROOT_WHEEL_SPEC, _ROOT_WHEEL_BYTES))
    monkeypatch.setattr(installer, "download_to_temp", fake)
    return fake


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    """Point the installer's app home at an isolated directory."""
    monkeypatch.setattr(installer.paths, "ANKI_MINER_HOME", tmp_path)
    return tmp_path


@pytest.fixture
def synthetic_pack(monkeypatch) -> LanguagePack:
    """Serve :data:`_PACK` for code ``xx``, the real manifests for everything else."""
    real = installer.load_pack

    def _load(code: str):
        return _PACK if code == "xx" else real(code)

    monkeypatch.setattr(installer, "load_pack", _load)
    return _PACK


@pytest.fixture
def root_member_pack(monkeypatch) -> LanguagePack:
    """Serve :data:`_ROOT_PACK` for code ``xx`` (one component + a root member)."""
    monkeypatch.setattr(installer, "load_pack", lambda code: _ROOT_PACK if code == "xx" else None)
    return _ROOT_PACK


@pytest.fixture
def clean_syspath():
    """Restore ``sys.path`` exactly, whatever the test appended to it."""
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def _write_component(root: Path, comp: PackComponent) -> Path:
    """Lay down a sentinel-complete component directory under *root*."""
    directory = root / comp.import_name
    directory.mkdir(parents=True, exist_ok=True)
    for name in comp.sentinels:
        (directory / name).write_bytes(b"x")
    return directory


def _ko_model_component() -> PackComponent:
    pack = installer.load_pack("ko")
    assert pack is not None
    return next(comp for comp in pack.components if comp.import_name == "kiwipiepy_model")


def _install_legacy_ko_model(home: Path) -> Path:
    """Lay down the pre-pack ``ko_model/kiwipiepy_model`` tree."""
    return _write_component(installer.legacy_ko_model_root(), _ko_model_component())


# ----------------------------------------------------------------------


class TestRoots:
    def test_a_pack_root_is_a_per_language_dir_in_the_app_home(self, home) -> None:
        assert installer.language_pack_root("zh") == home / "language_packs" / "zh"

    def test_the_root_is_read_at_call_time_not_snapshotted_at_import(self, monkeypatch, tmp_path) -> None:
        # The test-home isolation fixtures redirect it like every other managed
        # directory, exactly as ko_model_root used to be redirected.
        monkeypatch.setattr(installer.paths, "ANKI_MINER_HOME", tmp_path / "elsewhere")
        assert installer.language_pack_root("ko") == tmp_path / "elsewhere" / "language_packs" / "ko"

    def test_the_legacy_ko_root_is_the_pre_pack_directory(self, home) -> None:
        assert installer.legacy_ko_model_root() == home / "ko_model"
        assert installer.legacy_ko_model_root(home / "other") == home / "other" / "ko_model"


class TestLoadPack:
    def test_ja_has_no_pack(self) -> None:
        assert installer.load_pack("ja") is None

    def test_ko_and_zh_load_their_manifests(self) -> None:
        for code in ("ko", "zh"):
            pack = installer.load_pack(code)
            assert pack is not None and pack.code == code

    def test_an_unknown_code_is_not_an_error(self) -> None:
        assert installer.load_pack("nope") is None

    def test_the_manifest_is_cached(self) -> None:
        assert installer.load_pack("zh") is installer.load_pack("zh")


class TestPackSupported:
    def test_a_language_without_a_pack_is_unsupported(self) -> None:
        assert installer.pack_supported("ja") is False

    def test_universal_artifacts_resolve_everywhere(self, synthetic_pack) -> None:
        assert installer.pack_supported("xx") is True

    def test_a_platform_the_table_does_not_list_is_unsupported(self, monkeypatch) -> None:
        comp = PackComponent(
            import_name="xxpkg",
            required=True,
            sentinels=("__init__.py",),
            per_platform={_OTHER_PLATFORM: _WHEEL_SPEC},
        )
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )

        assert installer.pack_supported("xx") is False

    def test_an_abi_pin_for_another_python_is_unsupported(self, monkeypatch) -> None:
        comp = PackComponent(
            import_name="xxpkg",
            required=True,
            sentinels=("__init__.py",),
            abi=(3, 999),
            per_platform={_THIS_PLATFORM: _WHEEL_SPEC},
        )
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )

        assert installer.pack_supported("xx") is False

    def test_an_optional_component_without_an_artifact_still_leaves_the_pack_supported(self, monkeypatch) -> None:
        # opencc's case: no wheel for this platform, but Chinese still mines.
        optional = PackComponent(
            import_name="xxopt",
            required=False,
            sentinels=("__init__.py",),
            per_platform={_OTHER_PLATFORM: _WHEEL_SPEC},
        )
        pack = LanguagePack(code="xx", approx_download_mb=1, components=(_WHEEL_COMPONENT, optional))
        monkeypatch.setattr(installer, "load_pack", lambda _code: pack)

        assert installer.pack_supported("xx") is True


class TestComponentSatisfied:
    def test_an_absent_directory_is_not_satisfied(self, home, synthetic_pack) -> None:
        assert installer.component_satisfied("xx", _WHEEL_COMPONENT) is False

    def test_every_sentinel_must_be_present(self, home, synthetic_pack) -> None:
        directory = installer.language_pack_root("xx") / "xxpkg"
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_bytes(b"x")  # data.txt never landed

        assert installer.component_satisfied("xx", _WHEEL_COMPONENT) is False

        (directory / "data.txt").write_bytes(b"x")
        assert installer.component_satisfied("xx", _WHEEL_COMPONENT) is True

    def test_an_importable_package_satisfies_without_any_pack(self, home) -> None:
        # A pip install of the language extra needs no pack at all.
        comp = PackComponent(import_name="json", required=True, sentinels=("nothing-here",), universal=_WHEEL_SPEC)
        assert installer.component_satisfied("xx", comp) is True

    def test_component_path_answers_from_disk_alone(self, home, synthetic_pack, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: object())
        assert installer.component_path("xx", "xxpkg") is None

        directory = _write_component(installer.language_pack_root("xx"), _WHEEL_COMPONENT)
        assert installer.component_path("xx", "xxpkg") == directory

    def test_component_path_is_none_for_a_name_the_pack_does_not_carry(self, home) -> None:
        assert installer.component_path("ko", "not_a_component") is None
        assert installer.component_path("ja", "anything") is None

    def test_a_dangling_backup_is_reconciled_back_into_place(self, home, synthetic_pack) -> None:
        # atomic_replace_dir's crash window: the promoted dir is gone and only
        # its .bak- sibling survives.
        backup = installer.language_pack_root("xx") / "xxpkg.bak-20260831000000000001"
        backup.mkdir(parents=True)
        for name in _WHEEL_COMPONENT.sentinels:
            (backup / name).write_bytes(b"x")

        assert installer.component_satisfied("xx", _WHEEL_COMPONENT) is True
        assert (installer.language_pack_root("xx") / "xxpkg" / "data.txt").is_file()


class TestPackProvidedImportsStayGated:
    """A package the pack itself provides must still clear the sentinel gate.

    Once ANY component is complete the pack root is on ``sys.path``, so
    ``find_spec`` answers from the pack's own copy. Taking that as the importable
    tier's answer would report a half-deleted component installed: the row says
    "Installed", the button is disabled, and mining dies on ModuleNotFoundError
    with nothing in the app able to repair it.
    """

    def test_a_quarantined_root_member_is_not_hidden_by_the_packs_own_import(
        self, home, root_member_pack, clean_syspath
    ) -> None:
        root = installer.language_pack_root("xx")
        _write_component(root, _ROOT_COMPONENT)
        native = root / "_xxroot.abi3.so"
        native.write_bytes(b"native")
        sys.path.append(str(root))
        importlib.invalidate_caches()

        assert installer.component_satisfied("xx", _ROOT_COMPONENT) is True
        assert installer.is_installed("xx") is True

        native.unlink()  # e.g. an antivirus quarantine of the compiled extension
        importlib.invalidate_caches()

        # find_spec still resolves xxroot - out of the pack root, which is why it
        # cannot be the answer.
        assert importlib.util.find_spec("xxroot") is not None
        assert installer.component_satisfied("xx", _ROOT_COMPONENT) is False
        assert installer.is_installed("xx") is False

    def test_a_pack_root_namespace_package_is_pack_provided(self, home, root_member_pack, clean_syspath) -> None:
        # The sentinel gone leaves a dir with no __init__.py: find_spec answers
        # with a namespace package, whose origin is None and whose identity is
        # only in submodule_search_locations.
        root = installer.language_pack_root("xx")
        directory = _write_component(root, _ROOT_COMPONENT)
        (root / "_xxroot.abi3.so").write_bytes(b"native")
        (directory / "__init__.py").unlink()
        sys.path.append(str(root))
        importlib.invalidate_caches()

        spec = importlib.util.find_spec("xxroot")
        assert spec is not None and spec.origin is None

        assert installer.component_satisfied("xx", _ROOT_COMPONENT) is False
        assert installer.is_installed("xx") is False

    def test_a_package_from_outside_the_pack_still_satisfies(
        self, home, root_member_pack, monkeypatch, tmp_path
    ) -> None:
        # The pip-install case the importable tier exists for: nothing on disk
        # under the pack root, and no download owed.
        site_packages = tmp_path / "site-packages" / "xxroot"
        site_packages.mkdir(parents=True)
        (site_packages / "__init__.py").write_bytes(b"")
        monkeypatch.setattr(
            installer,
            "find_spec",
            lambda _name: SimpleNamespace(
                origin=str(site_packages / "__init__.py"),
                submodule_search_locations=[str(site_packages)],
            ),
        )

        assert installer.component_satisfied("xx", _ROOT_COMPONENT) is True
        assert installer.is_installed("xx") is True

    def test_the_legacy_ko_root_counts_as_pack_provided(self, home, monkeypatch) -> None:
        model = _ko_model_component()
        legacy = _install_legacy_ko_model(home)
        (legacy / model.sentinels[0]).unlink()
        monkeypatch.setattr(
            installer,
            "find_spec",
            lambda _name: SimpleNamespace(
                origin=str(legacy / "__init__.py"),
                submodule_search_locations=[str(legacy)],
            ),
        )

        assert installer.component_satisfied("ko", model) is False


class TestLegacyKoTier:
    def test_the_pre_pack_ko_model_dir_satisfies_the_model_component(self, home, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        assert installer.component_satisfied("ko", _ko_model_component()) is False

        legacy = _install_legacy_ko_model(home)
        assert installer.component_satisfied("ko", _ko_model_component()) is True
        assert installer.component_path("ko", "kiwipiepy_model") == legacy

    def test_the_new_pack_dir_wins_over_the_legacy_one(self, home, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        _install_legacy_ko_model(home)
        fresh = _write_component(installer.language_pack_root("ko"), _ko_model_component())

        assert installer.component_path("ko", "kiwipiepy_model") == fresh

    def test_the_legacy_tier_is_ko_only(self, home, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        _install_legacy_ko_model(home)
        model = _ko_model_component()

        assert installer.component_satisfied("zh", model) is False


class TestIsInstalled:
    def test_false_while_a_required_sentinel_is_missing(self, home, synthetic_pack, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        _write_component(installer.language_pack_root("xx"), _WHEEL_COMPONENT)
        partial = installer.language_pack_root("xx") / "xxmodel"
        partial.mkdir(parents=True)  # model.bin never landed

        assert installer.is_installed("xx") is False

    def test_true_once_every_required_component_is_there(self, home, synthetic_pack, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        for comp in _PACK.components:
            _write_component(installer.language_pack_root("xx"), comp)

        assert installer.is_installed("xx") is True

    def test_an_optional_component_is_not_required(self, home, monkeypatch) -> None:
        optional = PackComponent(import_name="xxopt", required=False, sentinels=("__init__.py",), universal=_WHEEL_SPEC)
        pack = LanguagePack(code="xx", approx_download_mb=1, components=(_WHEEL_COMPONENT, optional))
        monkeypatch.setattr(installer, "load_pack", lambda _code: pack)
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        _write_component(installer.language_pack_root("xx"), _WHEEL_COMPONENT)

        assert installer.is_installed("xx") is True

    def test_a_language_without_a_pack_is_never_installed(self, home) -> None:
        assert installer.is_installed("ja") is False


class TestInstall:
    def test_both_archive_kinds_are_extracted_and_promoted(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")

        result = installer.install_language_pack("xx", root)

        assert result == root
        assert (root / "xxpkg" / "__init__.py").read_bytes() == b"# package"
        assert (root / "xxpkg" / "sub" / "deep.txt").read_bytes() == b"deep"
        assert (root / "xxmodel" / "model.bin").read_bytes() == b"weights"
        assert installer.is_installed("xx") is True

    def test_excluded_and_out_of_prefix_members_never_land(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")

        installer.install_language_pack("xx", root)

        assert not (root / "xxpkg" / "tests").exists()
        assert not (root / "xxmodel" / "notes").exists()
        assert not (root / "xxpkg-1.0.dist-info").exists()
        assert not (root / "PKG-INFO").exists()
        assert not (root / "xxmodel-1.0").exists()

    def test_a_file_exclude_takes_only_the_exact_path(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        """``table.p`` goes, ``table.py`` stays — prefix matching ate both.

        jieba's manifest excludes the Jython ``.p`` pickles by exact filename;
        as prefixes those also matched the ``.py`` tables CPython imports, so an
        installed pack could not ``import jieba`` at all.
        """
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")

        installer.install_language_pack("xx", root)

        assert not (root / "xxpkg" / "table.p").exists()
        assert (root / "xxpkg" / "table.py").read_bytes() == b"# the real table"

    def test_a_satisfied_component_is_not_downloaded_again(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")

        installer.install_language_pack("xx", root)
        assert len(downloader.urls) == 2

        downloader.urls.clear()
        installer.install_language_pack("xx", root)

        assert downloader.urls == []

    def test_progress_is_labelled_with_the_pack_and_position(
        self, home, synthetic_pack, downloader, monkeypatch
    ) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        messages: list[str] = []

        installer.install_language_pack(
            "xx",
            installer.language_pack_root("xx"),
            progress=lambda done, total, msg: messages.append(msg),
        )

        assert messages == ["XX pack (1/2): downloading", "XX pack (2/2): downloading"]

    def test_the_resume_key_names_the_component_and_its_bytes(
        self, home, synthetic_pack, downloader, monkeypatch
    ) -> None:
        # A pin bump changes the sha and therefore the key, so a stale partial
        # can never be resumed into the new artifact (D16-C).
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)

        installer.install_language_pack("xx", installer.language_pack_root("xx"))

        assert downloader.resume_keys == [
            f"pack-xx-xxpkg-{_WHEEL_SPEC.sha256[:16]}",
            f"pack-xx-xxmodel-{_SDIST_SPEC.sha256[:16]}",
        ]

    def test_nothing_scratch_is_left_behind(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")

        installer.install_language_pack("xx", root)

        assert list(root.glob("*.part")) == []
        assert list(root.glob(".staging-*")) == []

    def test_orphans_from_a_crashed_run_are_swept(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")
        root.mkdir(parents=True)
        (root / "leftover.part").write_bytes(b"x" * 1024)
        orphan = root / ".staging-pack-xxpkg-abcd"
        orphan.mkdir()
        (orphan / "junk").write_bytes(b"junk")

        installer.install_language_pack("xx", root)

        assert list(root.glob("*.part")) == []
        assert list(root.glob(".staging-*")) == []
        assert installer.is_installed("xx") is True

    def test_reinstalling_replaces_the_existing_component(self, home, synthetic_pack, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")
        stale = root / "xxpkg"
        stale.mkdir(parents=True)
        (stale / "stale.txt").write_bytes(b"old")

        installer.install_language_pack("xx", root)

        assert not (stale / "stale.txt").exists()
        assert (stale / "data.txt").read_bytes() == b"payload"

    def test_an_optional_component_with_no_artifact_here_is_skipped(self, home, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        optional = PackComponent(
            import_name="xxopt",
            required=False,
            sentinels=("__init__.py",),
            per_platform={_OTHER_PLATFORM: _WHEEL_SPEC},
        )
        pack = LanguagePack(code="xx", approx_download_mb=1, components=(_WHEEL_COMPONENT, optional))
        monkeypatch.setattr(installer, "load_pack", lambda _code: pack)
        root = installer.language_pack_root("xx")

        installer.install_language_pack("xx", root)

        assert downloader.urls == [_WHEEL_SPEC.url]
        assert not (root / "xxopt").exists()


class TestRefusals:
    def test_a_language_without_a_pack_refuses(self, home, downloader) -> None:
        with pytest.raises(SetupError, match="no downloadable"):
            installer.install_language_pack("ja", installer.language_pack_root("ja"))

    def test_a_required_component_with_no_artifact_here_refuses(self, home, downloader, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        comp = PackComponent(
            import_name="xxpkg",
            required=True,
            sentinels=("__init__.py",),
            per_platform={_OTHER_PLATFORM: _WHEEL_SPEC},
        )
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )

        with pytest.raises(SetupError, match="not supported"):
            installer.install_language_pack("xx", installer.language_pack_root("xx"))

        assert downloader.urls == []

    def test_a_checksum_mismatch_promotes_nothing(self, home, monkeypatch, downloader) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        wrong = ArtifactSpec(
            url=_WHEEL_SPEC.url, sha256="0" * 64, kind="wheel", member_prefix="xxpkg/", exclude=("tests/",)
        )
        comp = PackComponent(import_name="xxpkg", required=True, sentinels=("__init__.py",), universal=wrong)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )
        root = installer.language_pack_root("xx")

        with pytest.raises(SetupError, match="checksum mismatch"):
            installer.install_language_pack("xx", root)

        assert not (root / "xxpkg").exists()
        assert list(root.glob("*.part")) == []

    @pytest.mark.parametrize("kind,prefix", [("wheel", "xxpkg/"), ("sdist", "xxmodel-1.0/xxmodel/")])
    def test_a_corrupt_archive_refuses(self, home, monkeypatch, downloader, kind, prefix) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        payload = b"not an archive at all"
        spec = _spec(payload, kind=kind, member_prefix=prefix)
        downloader.register((spec, payload))
        comp = PackComponent(import_name="xxpkg", required=True, sentinels=("__init__.py",), universal=spec)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )
        root = installer.language_pack_root("xx")

        with pytest.raises(SetupError, match="not a valid archive"):
            installer.install_language_pack("xx", root)

        assert not (root / "xxpkg").exists()

    def test_an_archive_without_the_member_prefix_refuses(self, home, monkeypatch, downloader) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        payload = _make_wheel({"other/__init__.py": b"x"})
        spec = _spec(payload, kind="wheel", member_prefix="xxpkg/")
        downloader.register((spec, payload))
        comp = PackComponent(import_name="xxpkg", required=True, sentinels=("__init__.py",), universal=spec)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )

        with pytest.raises(SetupError, match="no xxpkg/ payload"):
            installer.install_language_pack("xx", installer.language_pack_root("xx"))

    def test_an_archive_missing_a_sentinel_refuses(self, home, monkeypatch, downloader) -> None:
        # A wrong member_prefix or a repackaged artifact would otherwise promote
        # a directory the language cannot start from.
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        payload = _make_wheel({"xxpkg/__init__.py": b"x"})
        spec = _spec(payload, kind="wheel", member_prefix="xxpkg/")
        downloader.register((spec, payload))
        comp = PackComponent(import_name="xxpkg", required=True, sentinels=("__init__.py", "data.txt"), universal=spec)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )
        root = installer.language_pack_root("xx")

        with pytest.raises(SetupError, match="data.txt"):
            installer.install_language_pack("xx", root)

        assert not (root / "xxpkg").exists()

    @pytest.mark.parametrize("kind", ["wheel", "sdist"])
    def test_a_traversing_member_refuses(self, home, monkeypatch, downloader, kind) -> None:
        members = {"xxpkg/__init__.py": b"x", "xxpkg/../../escaped.py": b"pwned"}
        payload = _make_wheel(members) if kind == "wheel" else _make_sdist(members)
        spec = _spec(payload, kind=kind, member_prefix="xxpkg/")
        downloader.register((spec, payload))
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        comp = PackComponent(import_name="xxpkg", required=True, sentinels=("__init__.py",), universal=spec)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )
        root = installer.language_pack_root("xx")

        with pytest.raises(SetupError, match="unsafe path"):
            installer.install_language_pack("xx", root)

        assert not (root / "escaped.py").exists()
        assert not (root.parent / "escaped.py").exists()
        assert not (home / "escaped.py").exists()

    def test_cancelling_before_the_download_promotes_nothing(self, home, synthetic_pack, downloader) -> None:
        root = installer.language_pack_root("xx")

        with pytest.raises(OperationCancelled):
            installer.install_language_pack("xx", root, cancelled_check=lambda: True)

        assert downloader.urls == []
        assert not (root / "xxpkg").exists()

    def test_cancelling_after_the_download_cleans_the_part_file(
        self, home, synthetic_pack, downloader, monkeypatch
    ) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        cancelled = {"value": False}
        downloader.after_download = lambda: cancelled.__setitem__("value", True)
        root = installer.language_pack_root("xx")

        with pytest.raises(OperationCancelled):
            installer.install_language_pack("xx", root, cancelled_check=lambda: cancelled["value"])

        assert list(root.glob("*.part")) == []
        assert list(root.glob(".staging-*")) == []
        assert not (root / "xxpkg").exists()


class TestRootMembers:
    """Some wheels put a top-level module BESIDE the package dir.

    kiwipiepy is exactly that: ``_kiwipiepy.abi3.so`` (41 MB) sits at the wheel
    root and ``kiwipiepy/_wrap.py`` does ``import _kiwipiepy``. Extracting only
    the package dir promotes a component whose sentinels pass and whose import
    raises ModuleNotFoundError.
    """

    def test_a_root_member_lands_beside_the_component_dir(self, home, root_member_pack, downloader) -> None:
        root = installer.language_pack_root("xx")

        installer.install_language_pack("xx", root)

        assert (root / "xxroot" / "__init__.py").read_bytes() == b"# package"
        # The pack root IS the sys.path entry, so a top-level sibling module has
        # to sit here rather than inside the package dir.
        assert (root / "_xxroot.abi3.so").read_bytes() == b"native"
        assert not (root / "xxroot" / "_xxroot.abi3.so").exists()
        assert not (root / "xxroot-1.0.dist-info").exists()

    def test_a_component_missing_its_root_member_is_not_satisfied(self, home, root_member_pack, monkeypatch) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")
        _write_component(root, _ROOT_COMPONENT)

        assert installer.component_satisfied("xx", _ROOT_COMPONENT) is False
        assert installer.component_path("xx", "xxroot") is None
        assert installer.is_installed("xx") is False

        (root / "_xxroot.abi3.so").write_bytes(b"native")

        assert installer.component_satisfied("xx", _ROOT_COMPONENT) is True
        assert installer.is_installed("xx") is True

    def test_a_component_missing_its_root_member_is_repaired_by_reinstalling(
        self, home, root_member_pack, downloader, monkeypatch
    ) -> None:
        # Without the disk-tier check the component would count as satisfied
        # forever and no retry could ever fetch the extension module.
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        root = installer.language_pack_root("xx")
        _write_component(root, _ROOT_COMPONENT)

        installer.install_language_pack("xx", root)

        assert downloader.urls == [_ROOT_WHEEL_SPEC.url]
        assert (root / "_xxroot.abi3.so").read_bytes() == b"native"

    def test_an_archive_without_the_declared_root_member_refuses(self, home, monkeypatch, downloader) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        payload = _make_wheel({"xxroot/__init__.py": b"x"})  # the .so never shipped
        spec = _spec(payload, kind="wheel", member_prefix="xxroot/", root_members=("_xxroot.",))
        downloader.register((spec, payload))
        comp = PackComponent(import_name="xxroot", required=True, sentinels=("__init__.py",), universal=spec)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )
        root = installer.language_pack_root("xx")

        with pytest.raises(SetupError, match=r"_xxroot\."):
            installer.install_language_pack("xx", root)

        assert not (root / "xxroot").exists()

    def test_a_traversing_root_member_refuses(self, home, monkeypatch, downloader) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        payload = _make_wheel({"xxroot/__init__.py": b"x", "_xxroot/../../escaped.so": b"pwned"})
        spec = _spec(payload, kind="wheel", member_prefix="xxroot/", root_members=("_xxroot",))
        downloader.register((spec, payload))
        comp = PackComponent(import_name="xxroot", required=True, sentinels=("__init__.py",), universal=spec)
        monkeypatch.setattr(
            installer, "load_pack", lambda _code: LanguagePack(code="xx", approx_download_mb=1, components=(comp,))
        )
        root = installer.language_pack_root("xx")

        with pytest.raises(SetupError, match="unsafe path"):
            installer.install_language_pack("xx", root)

        assert not (root / "escaped.so").exists()
        assert not (root.parent / "escaped.so").exists()
        assert not (home / "escaped.so").exists()


class TestCustomInstallRoot:
    """``root`` steers the skip logic, not only where the bytes land.

    Task 6's CI seed script installs into ``$RUNNER_TEMP/lang_pack_seeds/<code>``.
    Reading satisfaction from the canonical root there would re-download every
    run, or (worse, on a runner with the extra pip-installed) seed nothing.
    """

    def test_satisfaction_reads_the_root_it_is_given(self, home, synthetic_pack, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        seed = tmp_path / "seed"
        _write_component(installer.language_pack_root("xx"), _WHEEL_COMPONENT)

        assert installer.component_satisfied("xx", _WHEEL_COMPONENT) is True
        assert installer.component_satisfied("xx", _WHEEL_COMPONENT, seed) is False

        _write_component(seed, _WHEEL_COMPONENT)

        assert installer.component_satisfied("xx", _WHEEL_COMPONENT, seed) is True

    def test_a_custom_root_never_answers_from_the_importable_tier(self, home, tmp_path) -> None:
        comp = PackComponent(import_name="json", required=True, sentinels=("nothing-here",), universal=_WHEEL_SPEC)

        assert installer.component_satisfied("xx", comp) is True
        assert installer.component_satisfied("xx", comp, installer.language_pack_root("xx")) is True
        assert installer.component_satisfied("xx", comp, tmp_path / "seed") is False

    def test_seeding_downloads_even_when_the_canonical_root_already_has_it(
        self, home, synthetic_pack, downloader, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        for comp in _PACK.components:
            _write_component(installer.language_pack_root("xx"), comp)
        seed = tmp_path / "seed"

        installer.install_language_pack("xx", seed)

        assert len(downloader.urls) == 2
        assert (seed / "xxpkg" / "data.txt").read_bytes() == b"payload"
        assert (seed / "xxmodel" / "model.bin").read_bytes() == b"weights"

    def test_a_second_seed_into_the_same_root_downloads_nothing(
        self, home, synthetic_pack, downloader, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        seed = tmp_path / "seed"

        installer.install_language_pack("xx", seed)
        assert len(downloader.urls) == 2
        downloader.urls.clear()

        installer.install_language_pack("xx", seed)

        assert downloader.urls == []

    def test_is_installed_stays_canonical(self, home, synthetic_pack, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(installer, "find_spec", lambda _name: None)
        for comp in _PACK.components:
            _write_component(tmp_path / "seed", comp)

        assert installer.is_installed("xx") is False


class TestSysPathInjection:
    def test_an_installed_pack_root_is_appended_last(self, home, clean_syspath) -> None:
        # Appended, never inserted: the pack dir must not shadow a same-named
        # module already on the path.
        zh = installer.load_pack("zh")
        assert zh is not None
        _write_component(installer.language_pack_root("zh"), zh.components[0])

        installer.ensure_language_packs_on_syspath()

        assert sys.path[-1] == str(installer.language_pack_root("zh"))

    def test_it_is_idempotent(self, home, clean_syspath) -> None:
        zh = installer.load_pack("zh")
        assert zh is not None
        _write_component(installer.language_pack_root("zh"), zh.components[0])

        installer.ensure_language_packs_on_syspath()
        installer.ensure_language_packs_on_syspath()

        assert sys.path.count(str(installer.language_pack_root("zh"))) == 1

    def test_an_empty_pack_dir_is_not_added(self, home, clean_syspath) -> None:
        installer.language_pack_root("zh").mkdir(parents=True)
        before = list(sys.path)

        installer.ensure_language_packs_on_syspath()

        assert sys.path == before

    def test_the_legacy_ko_root_is_added_on_its_own_sentinels(self, home, clean_syspath) -> None:
        _install_legacy_ko_model(home)

        installer.ensure_language_packs_on_syspath()

        assert str(installer.legacy_ko_model_root()) in sys.path

    def test_the_import_caches_are_invalidated_once(self, home, clean_syspath, monkeypatch) -> None:
        calls: list[int] = []
        monkeypatch.setattr(installer.importlib, "invalidate_caches", lambda: calls.append(1))
        zh = installer.load_pack("zh")
        assert zh is not None
        _write_component(installer.language_pack_root("zh"), zh.components[0])

        installer.ensure_language_packs_on_syspath()
        assert calls == [1]

        installer.ensure_language_packs_on_syspath()
        assert calls == [1]  # nothing appended the second time

    def test_it_never_raises(self, home, clean_syspath, monkeypatch) -> None:
        # Boot-time injection must never be what stops the app from starting.
        def _boom(_code: str):
            raise OSError("unreadable home")

        monkeypatch.setattr(installer, "language_pack_root", _boom)
        before = list(sys.path)

        installer.ensure_language_packs_on_syspath()

        assert sys.path == before


class TestKoResolutionLadder:
    """The Korean model resolves from the package, the pack, or the legacy dir.

    Absorbed from ``tests/unit/languages/test_ko_model_pack.py``: the bundle
    ships the kiwipiepy ENGINE without its ~88 MB model, so both halves of the
    ladder matter — a dev/CI machine with ``kiwipiepy-model`` on ``sys.path``
    keeps working untouched, and a bundled install reaches the pack.
    """

    def test_the_installed_package_wins_and_lets_kiwi_resolve_itself(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: object())

        assert tokenizer.resolve_model_path() is None

    def test_the_pack_answers_when_the_package_is_absent(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        model = _write_component(installer.language_pack_root("ko"), _ko_model_component())

        assert tokenizer.resolve_model_path() == str(model)

    def test_the_legacy_directory_still_answers(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        legacy = _install_legacy_ko_model(home)

        assert tokenizer.resolve_model_path() == str(legacy)

    def test_a_half_written_pack_is_not_accepted(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        model = installer.language_pack_root("ko") / "kiwipiepy_model"
        model.mkdir(parents=True)
        (model / "sj.morph").write_bytes(b"x")  # the other sentinels never landed

        with pytest.raises(ImportError):
            tokenizer.resolve_model_path()

    def test_neither_names_the_in_app_download(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)

        with pytest.raises(ImportError) as excinfo:
            tokenizer.resolve_model_path()

        message = str(excinfo.value)
        assert "Download" in message
        assert "Mining Language" in message
        assert "pip install" not in message

    def test_a_frozen_build_names_the_pack_not_pip(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import tokenizer

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        with pytest.raises(ImportError) as excinfo:
            tokenizer.resolve_model_path()

        assert str(excinfo.value) == (
            "Korean mining needs the Korean language pack. Download it in Settings -> Mining Language."
        )

    def test_the_provider_chains_the_missing_model_into_its_value_error(self, home, monkeypatch) -> None:
        # The contract every caller writes is ``except ValueError``; a bare
        # ImportError would escape it, so the reason has to arrive chained.
        from anki_miner.languages.ko import tokenizer
        from anki_miner.languages.tagger_provider import _build

        monkeypatch.setattr(tokenizer, "find_spec", lambda _name: None)

        with pytest.raises(ValueError) as excinfo:
            _build("ko")

        assert "No tokenizer registered" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ImportError)
        assert "Mining Language" in str(excinfo.value.__cause__)


class TestKoAvailability:
    """Also absorbed: the availability gate reads the same three tiers."""

    def test_the_pack_alone_satisfies_the_model_requirement(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import availability

        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        _write_component(installer.language_pack_root("ko"), _ko_model_component())

        assert availability.ko_missing_required_reason() is None

    def test_without_package_or_pack_the_reason_names_the_download(self, home, monkeypatch) -> None:
        from anki_miner.languages.ko import availability

        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())

        reason = availability.ko_missing_required_reason() or ""

        assert "Download" in reason
        assert "Mining Language" in reason
        assert "pip install" not in reason

    def test_a_missing_engine_still_points_at_the_extra(self, home, monkeypatch) -> None:
        # The pack carries the model, never the engine: without kiwipiepy there
        # is nothing to download in-app and pip is still the answer.
        from anki_miner.languages.ko import availability

        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy" else object())
        _write_component(installer.language_pack_root("ko"), _ko_model_component())

        reason = availability.ko_missing_required_reason() or ""

        assert "kiwipiepy" in reason
        assert 'pip install "anki-miner[ko]"' in reason

    def test_the_selector_offers_ko_once_the_pack_is_installed(self, home, monkeypatch) -> None:
        from anki_miner.gui.utils import language_choices
        from anki_miner.languages.ko import availability

        monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "kiwipiepy_model" else object())
        _write_component(installer.language_pack_root("ko"), _ko_model_component())

        assert "ko" in [code for code, _name in language_choices.available_mining_languages()]
