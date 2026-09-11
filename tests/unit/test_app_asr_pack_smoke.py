"""``ANKI_MINER_SMOKE=asr-absent``: the bare bundle must report every pack package absent.

The guard against the PYZ-halves failure (release.yml, .deb step): a package
stripped after Analysis leaves its pure-Python half in the PYZ, ``find_spec``
lies, and the Download button can never work. The handler reads the component
list from the manifest so a new component is asserted absent automatically.
"""

from __future__ import annotations

from unittest.mock import patch

from anki_miner.gui.app import _run_asr_pack_absent_bundled_smoke
from anki_miner.services.asr.asr_pack import PACK


def _find_spec_returning_for(present: set[str]):
    def fake(name: str, *args, **kwargs):
        return object() if name in present else None

    return fake


def test_passes_when_every_pack_package_is_absent(capsys) -> None:
    with (
        patch("anki_miner.gui.app.importlib.util.find_spec", _find_spec_returning_for(set())),
        patch("anki_miner.services.asr._engine.available", return_value=False),
    ):
        rc = _run_asr_pack_absent_bundled_smoke()

    assert rc == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out


def test_fails_naming_the_packages_still_in_the_bundle(capsys) -> None:
    with (
        patch("anki_miner.gui.app.importlib.util.find_spec", _find_spec_returning_for({"yaml", "huggingface_hub"})),
        patch("anki_miner.services.asr._engine.available", return_value=False),
    ):
        rc = _run_asr_pack_absent_bundled_smoke()

    assert rc != 0
    err = capsys.readouterr().err
    assert "BUNDLED_SMOKE_FAIL" in err
    assert "huggingface_hub" in err and "yaml" in err


def test_fails_when_the_engine_probe_disagrees(capsys) -> None:
    """available() is the probe the app trusts; a bundle where it lies must fail too."""
    with (
        patch("anki_miner.gui.app.importlib.util.find_spec", _find_spec_returning_for(set())),
        patch("anki_miner.services.asr._engine.available", return_value=True),
    ):
        rc = _run_asr_pack_absent_bundled_smoke()

    assert rc != 0
    assert "BUNDLED_SMOKE_FAIL" in capsys.readouterr().err


def test_probes_every_manifest_component_and_hf_xet() -> None:
    seen: list[str] = []

    def recording(name: str, *args, **kwargs):
        seen.append(name)
        return None

    with (
        patch("anki_miner.gui.app.importlib.util.find_spec", recording),
        patch("anki_miner.services.asr._engine.available", return_value=False),
    ):
        _run_asr_pack_absent_bundled_smoke()

    assert seen == [comp.import_name for comp in PACK.components] + ["hf_xet"]
