"""The ASR adapter over the generic pack core (no network, synthetic manifest)."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.languages.pack_spec import ArtifactSpec, DependencyPack, PackComponent
from anki_miner.services import pack_installer as core
from anki_miner.services.asr import asr_pack_installer as installer
from anki_miner.services.asr.asr_pack import PACK


def _wheel(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data in members.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


def _component(name: str, payload: bytes) -> PackComponent:
    return PackComponent(
        import_name=name,
        required=True,
        sentinels=("__init__.py",),
        universal=ArtifactSpec(
            url=f"https://files.pythonhosted.org/packages/xx/{name}-1.0-py3-none-any.whl",
            sha256=hashlib.sha256(payload).hexdigest(),
            kind="wheel",
            member_prefix=f"{name}/",
        ),
    )


_ENGINE_WHEEL = _wheel({"xxengine/__init__.py": b""})
_FRONT_WHEEL = _wheel({"xxfront/__init__.py": b""})
_ENGINE = _component("xxengine", _ENGINE_WHEEL)
_FRONT = _component("xxfront", _FRONT_WHEEL)
_SYNTHETIC = DependencyPack(name="asr", approx_download_mb=1, components=(_ENGINE, _FRONT))


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setattr(installer.paths, "ANKI_MINER_HOME", tmp_path)
    return tmp_path


@pytest.fixture
def synthetic_pack(monkeypatch) -> DependencyPack:
    monkeypatch.setattr(installer, "PACK", _SYNTHETIC)
    return _SYNTHETIC


@pytest.fixture
def downloader(monkeypatch):
    payloads = {_ENGINE.universal.url: _ENGINE_WHEEL, _FRONT.universal.url: _FRONT_WHEEL}
    urls: list[str] = []

    def fake(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None, resume_key=None, resume_root=None):
        urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / f"artifact-{len(urls)}.part"
        part.write_bytes(payloads[url])
        return part

    monkeypatch.setattr(core, "download_to_temp", fake)
    return urls


@pytest.fixture
def clean_syspath():
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def _write_component(root: Path, comp: PackComponent) -> None:
    (root / comp.import_name).mkdir(parents=True, exist_ok=True)
    for name in comp.sentinels:
        (root / comp.import_name / name).write_bytes(b"x")


class TestRoot:
    def test_the_root_is_asr_pack_in_the_app_home_read_at_call_time(self, home) -> None:
        assert installer.asr_pack_root() == home / "asr_pack"

    def test_the_real_manifest_is_exported(self) -> None:
        assert installer.PACK is PACK


class TestSupport:
    def test_supported_follows_the_core_rule_over_the_real_manifest(self, monkeypatch) -> None:
        calls: list[object] = []
        monkeypatch.setattr(
            installer, "components_supported", lambda components: calls.append(tuple(components)) or True
        )
        assert installer.asr_pack_supported()
        assert calls == [PACK.components]

    def test_unsupported_on_this_interpreter_when_the_cp312_pin_misses(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "version_info", (3, 99, 0, "final", 0))
        assert not installer.asr_pack_supported()


class TestSatisfaction:
    def test_nothing_installed_and_nothing_importable(self, home, synthetic_pack, monkeypatch) -> None:
        monkeypatch.setattr(core, "find_spec", lambda _name: None)
        assert not installer.is_installed()
        assert not installer.component_satisfied(_ENGINE)

    def test_a_complete_pack_dir_satisfies(self, home, synthetic_pack, monkeypatch) -> None:
        monkeypatch.setattr(core, "find_spec", lambda _name: None)
        _write_component(installer.asr_pack_root(), _ENGINE)
        _write_component(installer.asr_pack_root(), _FRONT)
        assert installer.is_installed()

    def test_a_pip_install_outside_the_pack_satisfies(self, home, synthetic_pack, monkeypatch, tmp_path) -> None:
        elsewhere = tmp_path / "site-packages" / "xxengine" / "__init__.py"
        monkeypatch.setattr(
            core,
            "find_spec",
            lambda _name: type("S", (), {"origin": str(elsewhere), "submodule_search_locations": None})(),
        )
        assert installer.component_satisfied(_ENGINE)

    def test_a_resolution_inside_the_pack_root_does_not_count(self, home, synthetic_pack, monkeypatch) -> None:
        inside = installer.asr_pack_root() / "xxengine" / "__init__.py"
        monkeypatch.setattr(
            core,
            "find_spec",
            lambda _name: type("S", (), {"origin": str(inside), "submodule_search_locations": None})(),
        )
        assert not installer.component_satisfied(_ENGINE)

    def test_a_non_canonical_root_answers_from_disk_only(self, home, synthetic_pack, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(core, "find_spec", lambda _name: object())
        seed = tmp_path / "seed"
        assert not installer.component_satisfied(_ENGINE, seed)
        _write_component(seed, _ENGINE)
        assert installer.component_satisfied(_ENGINE, seed)


class TestInstall:
    def test_installs_every_missing_component_into_the_root(
        self, home, synthetic_pack, downloader, monkeypatch
    ) -> None:
        monkeypatch.setattr(core, "find_spec", lambda _name: None)
        root = installer.install_asr_pack(installer.asr_pack_root())
        assert root == installer.asr_pack_root()
        assert (root / "xxengine" / "__init__.py").is_file()
        assert (root / "xxfront" / "__init__.py").is_file()
        assert downloader == [_ENGINE.universal.url, _FRONT.universal.url]
        assert installer.is_installed()

    def test_a_seed_into_a_scratch_root_skips_what_is_already_there(
        self, home, synthetic_pack, downloader, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(core, "find_spec", lambda _name: object())  # a runner with [asr] installed
        seed = tmp_path / "seed"
        _write_component(seed, _ENGINE)
        installer.install_asr_pack(seed)
        assert downloader == [_FRONT.universal.url]

    def test_unsupported_platform_refuses_before_downloading(self, home, downloader, monkeypatch) -> None:
        monkeypatch.setattr(core, "find_spec", lambda _name: None)
        monkeypatch.setattr(sys, "version_info", (3, 99, 0, "final", 0))
        with pytest.raises(SetupError, match="ASR engine pack is not supported"):
            installer.install_asr_pack(installer.asr_pack_root())
        assert downloader == []


class TestSyspath:
    def test_an_empty_or_absent_root_is_not_added(self, home, synthetic_pack, clean_syspath) -> None:
        before = list(sys.path)
        installer.ensure_asr_pack_on_syspath()
        assert sys.path == before
        installer.asr_pack_root().mkdir(parents=True)
        installer.ensure_asr_pack_on_syspath()
        assert sys.path == before

    def test_a_root_with_one_complete_component_is_appended_once_and_caches_invalidated_each_time(
        self, home, synthetic_pack, clean_syspath, monkeypatch
    ) -> None:
        calls: list[int] = []
        monkeypatch.setattr(installer.importlib, "invalidate_caches", lambda: calls.append(1))
        _write_component(installer.asr_pack_root(), _FRONT)

        installer.ensure_asr_pack_on_syspath()
        installer.ensure_asr_pack_on_syspath()

        assert sys.path[-1] == str(installer.asr_pack_root())
        assert sys.path.count(str(installer.asr_pack_root())) == 1
        # Invalidated on every call the root is on the path for, not only the
        # appending one: the in-session resume case below depends on it.
        assert calls == [1, 1]

    def test_a_component_added_to_an_already_appended_root_becomes_findable(
        self, home, synthetic_pack, clean_syspath
    ) -> None:
        """Append first (partial pack from a cancelled install), install more, re-probe."""
        root = installer.asr_pack_root()
        _write_component(root, _FRONT)
        installer.ensure_asr_pack_on_syspath()
        assert importlib.util.find_spec("xxfront") is not None
        assert importlib.util.find_spec("xxengine") is None  # the finder now caches this miss

        _write_component(root, _ENGINE)
        installer.ensure_asr_pack_on_syspath()

        assert importlib.util.find_spec("xxengine") is not None

    def test_it_never_raises(self, home, clean_syspath, monkeypatch) -> None:
        def _boom():
            raise OSError("unreadable home")

        monkeypatch.setattr(installer, "asr_pack_root", _boom)
        before = list(sys.path)
        installer.ensure_asr_pack_on_syspath()
        assert sys.path == before


def test_find_spec_sees_an_appended_root_without_a_stale_importer_cache(
    home, synthetic_pack, clean_syspath, tmp_path
) -> None:
    """The boot and in-session paths both rely on this: a fresh sys.path entry
    has no sys.path_importer_cache slot, so PathFinder builds its FileFinder on
    first use, and invalidate_caches() covers a root that existed empty before."""
    root = installer.asr_pack_root()
    root.mkdir(parents=True)
    assert importlib.util.find_spec("xxfront") is None
    _write_component(root, _FRONT)
    installer.ensure_asr_pack_on_syspath()
    assert importlib.util.find_spec("xxfront") is not None
