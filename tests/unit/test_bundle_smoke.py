"""Tests for the shared frozen-bundle smoke runner."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_bundle_smoke_uses_one_temporary_anki_miner_home(tmp_path: Path) -> None:
    dist = tmp_path / "dist" / "AnkiMiner"
    dist.mkdir(parents=True)
    record = tmp_path / "probe-homes.txt"
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    (caller_home / "sentinel").write_text("keep", encoding="utf-8")

    app = dist / "AnkiMiner"
    app.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -d "$ANKI_MINER_HOME"\n'
        'printf \'%s\\n\' "$ANKI_MINER_HOME" >> "$SMOKE_HOME_RECORD"\n'
        'touch "$ANKI_MINER_HOME/probe-touch"\n'
        'if [ "${ANKI_MINER_ASR_VULKAN_PROBE:-}" = 1 ]; then\n'
        "  echo 0\n"
        'elif [ "${ANKI_MINER_MPV_PROBE:-}" = 1 ]; then\n'
        "  echo MPV_PROBE_OK\n"
        "else\n"
        '  case "${ANKI_MINER_SMOKE:-}" in\n'
        "    youtube|asr|whispercpp) echo BUNDLED_SMOKE_PASS ;;\n"
        "    *) exit 3 ;;\n"
        "  esac\n"
        "fi\n",
        encoding="utf-8",
    )
    app.chmod(0o755)

    ffmpeg = dist / "ffmpeg"
    ffmpeg.write_text(
        "#!/usr/bin/env bash\necho 'libmp3lame libopus libsvtav1 libwebp libwebp_anim'\n",
        encoding="utf-8",
    )
    ffmpeg.chmod(0o755)
    for library in ("libggml-vulkan.so", "libggml-cpu.so", "libmpv.so.2"):
        (dist / library).touch()

    env = os.environ.copy()
    env.update(
        {
            "ANKI_MINER_HOME": str(caller_home),
            "BUNDLE_SMOKE_SKIP_ASR": "0",
            "BUNDLE_SMOKE_SKIP_MPV": "0",
            "BUNDLE_SMOKE_SKIP_WHISPERCPP": "0",
            "SMOKE_HOME_RECORD": str(record),
        }
    )
    env.pop("BUNDLE_SMOKE_GGML_MODEL", None)

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "bundle_smoke.sh"), str(dist)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    homes = record.read_text(encoding="utf-8").splitlines()
    assert len(homes) == 5
    assert len(set(homes)) == 1
    assert homes[0] != str(caller_home)
    assert not Path(homes[0]).exists()
    assert not (caller_home / "probe-touch").exists()
    assert (caller_home / "sentinel").read_text(encoding="utf-8") == "keep"
