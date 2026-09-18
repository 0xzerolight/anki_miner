"""The shared spaCy runtime pack: generated, excluded from bundles, never shadowing the bundle's own pins."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from anki_miner.services.language_pack_installer import load_pack
from tests.unit.languages.test_zh_bundling import _list_body

ROOT = Path(__file__).resolve().parents[3]
RESOLVED = ROOT / "tests" / "fixtures" / "language_packs" / "_spacy.resolved.json"

#: Every spaCy model package the repo pins (Stage SP pre-lands their excludes and CI wheels).
SPACY_MODEL_PACKAGES = (
    "ca_core_news_sm",
    "de_core_news_sm",
    "el_core_news_sm",
    "en_core_web_sm",
    "es_core_news_sm",
    "fi_core_news_sm",
    "fr_core_news_sm",
    "hr_core_news_sm",
    "hu_core_news_md",
    "it_core_news_sm",
    "nb_core_news_sm",
    "nl_core_news_sm",
    "pl_core_news_sm",
    "pt_core_news_sm",
    "ro_core_news_sm",
    "sv_core_news_sm",
)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_the_runtime_pack_is_shared_and_carries_spacy():
    pack = load_pack("_spacy")
    assert pack is not None and pack.code == "_spacy" and pack.requires == ()
    names = {comp.import_name for comp in pack.components}
    assert {"spacy", "thinc", "blis", "srsly", "preshed", "cymem", "murmurhash", "pydantic"} <= names


#: Lock pins only the [asr] extra's closure brings. The frozen bundle carries none of them (the ASR pack owns
#: httpx/httpcore/h11/anyio; typer, shellingham and annotated_doc arrived with huggingface_hub), yet weasel
#: imports typer and httpx at `import spacy`, so the runtime pack must carry them. Any OTHER lock pin is bundle
#: content the pack must not shadow.
_LOCK_PINS_THE_BUNDLE_DOES_NOT_CARRY = frozenset(
    {"annotated-doc", "anyio", "h11", "httpcore", "httpx", "shellingham", "typer"}
)


def test_no_component_shadows_a_bundle_pin():
    lock = {
        _canonical(line.split("==", 1)[0])
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }
    pack = load_pack("_spacy")
    assert pack is not None
    assert {_canonical(comp.import_name) for comp in pack.components} & lock == _LOCK_PINS_THE_BUNDLE_DOES_NOT_CARRY


#: Win32-only closure members the Windows bundle already ships, so the spec must not exclude them: tqdm and
#: click (both requirements.lock pins) depend on colorama under a Windows marker, which is why the lock does
#: not name it. The generator counts them in the win32 size only; the bundle's copy wins over the pack's.
_BUNDLED_THROUGH_A_PLATFORM_MARKER = frozenset({"colorama"})
_WINDOWS = "win32/AMD64"


def test_every_pack_package_and_every_model_is_excluded_from_the_bundle():
    excludes = _list_body("excludes")
    pack = load_pack("_spacy")
    assert pack is not None
    names = [comp.import_name for comp in pack.components]
    assert set(names) >= _BUNDLED_THROUGH_A_PLATFORM_MARKER  # an exception the manifest no longer needs goes
    for name in [name for name in names if name not in _BUNDLED_THROUGH_A_PLATFORM_MARKER] + list(SPACY_MODEL_PACKAGES):
        assert f'"{name}",' in excludes, f"anki_miner.spec does not exclude {name}"
    for name in _BUNDLED_THROUGH_A_PLATFORM_MARKER:
        assert f'"{name}",' not in excludes, f"anki_miner.spec must not exclude {name}"


def test_size_report_every_platform_fits_the_advertised_download():
    resolved = json.loads(RESOLVED.read_text(encoding="utf-8"))
    totals: dict[str, int] = {}
    for comp in resolved["components"]:
        if comp["universal"] is not None:
            for key in ("linux/x86_64", "linux/aarch64", _WINDOWS, "darwin/arm64", "darwin/x86_64"):
                if key != _WINDOWS and comp["import_name"] in _BUNDLED_THROUGH_A_PLATFORM_MARKER:
                    continue
                totals[key] = totals.get(key, 0) + comp["universal"]["size"]
        else:
            for key, artifact in comp["per_platform"].items():
                totals[key] = totals.get(key, 0) + artifact["size"]
    assert len(totals) == 5
    assert max(totals.values()) <= resolved["approx_download_mb"] * 1_000_000
    assert resolved["approx_download_mb"] <= 100  # the ko model pack precedent


#: The uv target's glibc. The macOS floor is asserted for every pack in
#: tests/unit/services/test_pinned_wheel_macos_floors.py, which lists ``_spacy``.
_MANYLINUX_FLOOR = (2, 28)
_MANYLINUX = re.compile(r"manylinux_(\d+)_(\d+)|manylinux(2014|2010|1)")
_LEGACY_MANYLINUX = {"2014": (2, 17), "2010": (2, 12), "1": (2, 5)}


def _glibc_floor(filename: str) -> tuple[int, int] | None:
    floors = [
        (int(m.group(1)), int(m.group(2))) if m.group(1) else _LEGACY_MANYLINUX[m.group(3)]
        for m in _MANYLINUX.finditer(filename)
    ]
    return min(floors) if floors else None


def test_every_linux_wheel_loads_on_the_glibc_floor():
    """Packs honour no OS-version tag (memory pinned-wheel-macos-floor): the manifest must not outrun the app."""
    pack = load_pack("_spacy")
    assert pack is not None
    for comp in pack.components:
        for (os_name, machine), spec in (comp.per_platform or {}).items():
            filename = spec.url.rsplit("/", 1)[-1]
            if os_name == "linux":
                floor = _glibc_floor(filename)
                assert floor is not None and floor <= _MANYLINUX_FLOOR, (comp.import_name, machine, filename)


def test_mypy_ignores_the_untyped_engine_modules():
    overrides = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]["overrides"]
    ignored = {module for entry in overrides if entry.get("ignore_missing_imports") for module in entry["module"]}
    assert {"spacy.*", "thinc.*"} <= ignored
