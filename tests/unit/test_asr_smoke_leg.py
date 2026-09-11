"""The bundle smoke's two ASR legs: bare-absent (always) and seeded (with a pack seed).

Mirrors ``tests/unit/languages/test_ko_smoke_leg.py``: a fake ``AnkiMiner``
records what it was handed, and the shell script's literal paths are pinned to
the installer's own root so the two cannot drift.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from anki_miner.config import paths
from anki_miner.services.asr import asr_pack_installer

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "bundle_smoke.sh"


def test_the_seed_writes_where_the_installer_reads() -> None:
    relative = asr_pack_installer.asr_pack_root().relative_to(paths.ANKI_MINER_HOME).as_posix()
    assert relative == "asr_pack"
    text = SMOKE.read_text(encoding="utf-8")
    assert f'"$ANKI_MINER_HOME/{relative}"' in text


def test_the_release_workflow_seeds_the_asr_pack_and_runs_the_leg_on_every_platform() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'scripts/fetch_language_pack_seeds.py "$RUNNER_TEMP/lang_pack_seeds" zh ko asr' in workflow
    matrix = json.loads((ROOT / ".github" / "release-matrix.json").read_text(encoding="utf-8"))
    for entry in matrix:
        assert entry["skip_asr_smoke"] == "", entry["platform"]
    for comp in asr_pack_installer.PACK.components:
        for spec in [comp.universal] if comp.universal else list(comp.per_platform.values()):
            assert spec.sha256 not in workflow


def test_the_preflight_seeds_the_pack_and_hands_the_seed_dir_to_the_smoke() -> None:
    preflight = (ROOT / "scripts" / "release_preflight.sh").read_text(encoding="utf-8")
    assert 'scripts/fetch_language_pack_seeds.py "$CACHE/pack_seeds" asr' in preflight
    assert 'BUNDLE_SMOKE_PACK_SEEDS="$CACHE/pack_seeds"' in preflight


def test_the_dry_run_gate_requires_both_asr_legs_to_have_executed() -> None:
    """A wrong pin must not turn into SKIP-everywhere and a green release (judge M2)."""
    dryrun = (ROOT / "scripts" / "release_dryrun.sh").read_text(encoding="utf-8")
    smoke = SMOKE.read_text(encoding="utf-8")
    for marker in ("BUNDLED_ASR_ABSENT_PASS", "BUNDLED_ASR_PACK_PASS"):
        assert smoke.count(f'echo "{marker}"') == 1, marker
        assert marker in dryrun, marker
    assert 'grep -q "SKIP asr"' in dryrun
    for os_label in ("ubuntu-22.04", "windows-latest", "macos-latest", "macos-15-intel"):
        assert f'assert_asr_ran "{os_label}"' in dryrun


def _load_seeds_script():
    """scripts/ is not a package; load it the way test_readme_i18n.py does."""
    import importlib.util
    import sys

    script = ROOT / "scripts" / "fetch_language_pack_seeds.py"
    spec = importlib.util.spec_from_file_location("fetch_language_pack_seeds", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_language_pack_seeds"] = module
    spec.loader.exec_module(module)
    return module


def test_a_client_error_on_the_pin_fails_the_seed_closed(tmp_path: Path, monkeypatch) -> None:
    """404 = deterministically wrong pin; only transport failures may fall open."""
    import requests

    from anki_miner.exceptions import DownloadFailed

    seeds = _load_seeds_script()

    def _raise(status: int):
        response = requests.Response()
        response.status_code = status
        http_error = requests.HTTPError(response=response)

        def _install(root):
            raise DownloadFailed("Failed to download x") from http_error

        return _install

    monkeypatch.setattr(seeds.asr_pack_installer, "install_asr_pack", _raise(404))
    assert seeds.seed(["asr"], tmp_path) == 1

    monkeypatch.setattr(seeds.asr_pack_installer, "install_asr_pack", _raise(503))
    assert seeds.seed(["asr"], tmp_path) == 0

    def _transport(root):
        raise DownloadFailed("Failed to download x") from requests.ConnectionError("dns")

    monkeypatch.setattr(seeds.asr_pack_installer, "install_asr_pack", _transport)
    assert seeds.seed(["asr"], tmp_path) == 0


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist" / "AnkiMiner"
    dist.mkdir(parents=True)
    app = dist / "AnkiMiner"
    app.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\n\' "${ANKI_MINER_SMOKE:-probe}" >> "$SEED_RECORD"\n'
        'case "${ANKI_MINER_SMOKE:-}" in\n'
        "  asr)\n"
        '    test -f "$ANKI_MINER_HOME/asr_pack/faster_whisper/__init__.py"\n'
        '    test "${HF_HUB_OFFLINE:-}" = 1\n'
        "    echo BUNDLED_SMOKE_PASS ;;\n"
        "  asr-absent)\n"
        '    test ! -e "$ANKI_MINER_HOME/asr_pack"\n'
        "    echo BUNDLED_SMOKE_PASS ;;\n"
        "  youtube|whispercpp) echo BUNDLED_SMOKE_PASS ;;\n"
        "  *)\n"
        '    if [ "${ANKI_MINER_ASR_VULKAN_PROBE:-}" = 1 ]; then echo 0\n'
        '    elif [ "${ANKI_MINER_MPV_PROBE:-}" = 1 ]; then echo MPV_PROBE_OK\n'
        "    else exit 3\n"
        "    fi ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    app.chmod(0o755)
    ffmpeg = dist / "ffmpeg"
    ffmpeg.write_text(
        "#!/usr/bin/env bash\necho 'libmp3lame libopus libsvtav1 libwebp libwebp_anim'\n", encoding="utf-8"
    )
    ffmpeg.chmod(0o755)
    # The encoders leg also runs ffprobe (the shared ffmpeg build ships both);
    # same stub as tests/unit/languages/test_ko_smoke_leg.py.
    ffprobe = dist / "ffprobe"
    ffprobe.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    ffprobe.chmod(0o755)
    for library in ("libggml-vulkan.so", "libggml-cpu.so", "libmpv.so.2"):
        (dist / library).touch()
    return dist


def _seed_asr(seeds: Path) -> None:
    front = seeds / "asr" / "faster_whisper"
    front.mkdir(parents=True)
    (front / "__init__.py").write_bytes(b"")


def _run_smoke(tmp_path: Path, dist: Path, record: Path, seeds: Path | None, *, skip_asr: str = "0"):
    env = os.environ.copy()
    env.update(
        {
            "BUNDLE_SMOKE_SKIP_ASR": skip_asr,
            "BUNDLE_SMOKE_SKIP_MPV": "0",
            "BUNDLE_SMOKE_SKIP_WHISPERCPP": "0",
            "SEED_RECORD": str(record),
        }
    )
    env.pop("BUNDLE_SMOKE_GGML_MODEL", None)
    env.pop("BUNDLE_SMOKE_LANGS", None)
    env.pop("BUNDLE_SMOKE_YTDLP_SEED", None)
    if seeds is None:
        env.pop("BUNDLE_SMOKE_PACK_SEEDS", None)
    else:
        env["BUNDLE_SMOKE_PACK_SEEDS"] = str(seeds)
    return subprocess.run(
        ["bash", str(SMOKE), str(dist)], cwd=tmp_path, env=env, check=False, capture_output=True, text=True, timeout=60
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_the_bare_bundle_is_probed_before_the_seed_lands(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    record = tmp_path / "record.txt"
    seeds = tmp_path / "seeds"
    _seed_asr(seeds)

    result = _run_smoke(tmp_path, dist, record, seeds)

    assert result.returncode == 0, result.stdout + result.stderr
    modes = record.read_text(encoding="utf-8").splitlines()
    assert modes.index("asr-absent") < modes.index("asr")
    assert "PASS asr-absent" in result.stdout
    assert "PASS asr" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_without_a_seed_the_seeded_leg_skips_loudly_and_the_bare_leg_still_runs(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    record = tmp_path / "record.txt"

    result = _run_smoke(tmp_path, dist, record, None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS asr-absent" in result.stdout
    assert "SKIP asr" in result.stdout
    assert "::warning::" in result.stdout
    modes = record.read_text(encoding="utf-8").splitlines()
    assert "asr-absent" in modes
    assert "asr" not in modes


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_skip_asr_skips_only_the_seeded_leg(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    record = tmp_path / "record.txt"
    seeds = tmp_path / "seeds"
    _seed_asr(seeds)

    result = _run_smoke(tmp_path, dist, record, seeds, skip_asr="1")

    assert result.returncode == 0, result.stdout + result.stderr
    modes = record.read_text(encoding="utf-8").splitlines()
    assert "asr-absent" in modes
    assert "asr" not in modes
    assert "SKIP asr (BUNDLE_SMOKE_SKIP_ASR=1" in result.stdout


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is unavailable")
def test_the_smoke_script_still_passes_shellcheck() -> None:
    result = subprocess.run(["shellcheck", str(SMOKE)], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
