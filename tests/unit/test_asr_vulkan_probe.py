"""Tests for anki_miner.services.asr._vulkan_probe — the subprocess GPU probe.

The probe is the child process spawned by ``_engine.vulkan_device_count`` to
count Vulkan devices via a cold ``ctypes`` call into ggml-vulkan. It is
isolated in a subprocess precisely because a broken Vulkan driver can C-abort
uncatchably — the abort then kills only the child, and the parent reads a clean
0 (CPU). This dev env ships the CPU wheel (no ggml-vulkan lib), so the graceful
absent-loader path is exercised for real here, not mocked.
"""

import subprocess
import sys


def test_main_returns_zero_and_prints_zero_in_this_real_env(capsys):
    """In this env (no ggml-vulkan lib) main() prints '0' and returns 0 — real path."""
    from anki_miner.services.asr import _vulkan_probe

    rc = _vulkan_probe.main()

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "0"


def test_module_runs_as_subprocess_clean_zero():
    """Invoked as `python -m ...` the probe exits 0 with stdout '0'."""
    proc = subprocess.run(
        [sys.executable, "-m", "anki_miner.services.asr._vulkan_probe"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == "0"
