"""The ASR engine stays OUT of the frozen bundle; it arrives as the ASR pack.

Spec TEXT is parsed rather than executed, the same way
``tests/unit/languages/test_zh_bundling.py`` does it: PyInstaller is a build-time
tool and is not installed in this venv. The exclude set is read from the pack
manifest so a component added there cannot be forgotten here; the hidden-import
set is pinned literally because it is what the excludes silently take away
(huggingface_hub/httpx/anyio were the only importers of tqdm.auto, asyncio,
secrets ... in the frozen graph — see the plan's "Hidden imports" section).
"""

from __future__ import annotations

import json
from pathlib import Path

from anki_miner.services.asr.asr_pack import PACK
from tests.unit.languages.test_zh_bundling import _list_body

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PROJECT_ROOT / "PyInstaller-Hooks"

#: Every pack component, plus hf_xet: excluded (its absence stays a guarantee)
#: but never shipped — huggingface_hub only activates xet on a dist-info the
#: pack does not carry, so the wheel would be inert bytes.
PACKED = tuple(comp.import_name for comp in PACK.components) + ("hf_xet",)

#: Modules the pack packages import at module level that nothing left in the
#: base graph reaches once they are excluded. tqdm/__init__ never imports
#: .auto/.asyncio/.contrib (ffsubsync and pywhispercpp import bare tqdm);
#: secrets has no base importer. Missing one of those = ModuleNotFoundError
#: from the seeded pack. asyncio is belt-and-braces: typing_extensions imports
#: asyncio.coroutines function-locally and PyInstaller follows that.
HIDDEN = ("asyncio", "secrets", "tqdm.auto", "tqdm.autonotebook", "tqdm.asyncio", "tqdm.contrib.concurrent")


def test_every_pack_package_is_excluded_from_the_graph() -> None:
    excludes = _list_body("excludes")
    for name in PACKED:
        assert f'"{name}",' in excludes, f"anki_miner.spec does not exclude {name}"


def test_no_pack_package_is_pinned_into_the_import_graph() -> None:
    """A hiddenimport would drag the engine back in past the exclude."""
    hiddenimports = _list_body("hiddenimports")
    for name in PACKED:
        for pin in (f'"{name}"', f'"{name}.'):
            assert pin not in hiddenimports, f"anki_miner.spec still pins {name} into the graph"


def test_the_modules_the_pack_needs_from_the_base_stay_pinned() -> None:
    hiddenimports = _list_body("hiddenimports")
    for name in HIDDEN:
        assert f'"{name}",' in hiddenimports, f"anki_miner.spec no longer pins {name} for the ASR pack"


def test_the_collecting_hook_is_gone_and_the_whispercpp_hook_stays() -> None:
    """collect_all on faster_whisper/ctranslate2/av would repopulate what the excludes removed.

    pywhispercpp (the Vulkan whisper.cpp backend) is NOT part of the pack and its
    hook keeps grafting the ggml backend libs into the bundle.
    """
    assert not (HOOKS_DIR / "hook-faster_whisper.py").exists()
    assert (HOOKS_DIR / "hook-pywhispercpp.py").exists()


def test_the_release_matrix_still_installs_the_extra_so_the_excludes_are_exercised() -> None:
    """An exclude can only be proven by freezing on a machine that HAS the package.

    Every leg but Intel macOS installs [asr] (Intel's [asr] is unresolvable:
    onnxruntime has no x86_64-mac wheel), so the CI build itself proves the
    excludes work; the pack seed then proves the engine still runs from the pack.
    """
    matrix = json.loads((PROJECT_ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    by_platform = {entry["platform"]: entry for entry in matrix}
    for platform in ("linux", "windows", "macos-arm64"):
        assert by_platform[platform]["install_target"] == ".[asr]", platform
    assert by_platform["macos-intel"]["install_target"] == "."
    preflight = (PROJECT_ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert '".[asr,zh,ko]"' in preflight
