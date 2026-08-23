"""Regression test for Issue #112 against a REAL libmpv.

The rest of the loader suite fakes python-mpv, so it can only assert which
kwargs we pass — never what libmpv does with them. That gap is exactly how a
"fix" that passed only ``load-scripts=no`` shipped: the option is real, libmpv
accepts it, and mpv still loads every builtin Lua script (``--load-scripts``
gates the user scripts directory; the builtins come up from
``mp_load_builtin_scripts`` on the option-change callback — true through mpv
0.40; 0.41 gates the builtins too, see the control test). Any Lua at all means
LuaJIT, and LuaJIT means first-chance SEH 0xE24C4A02 on Windows for
faulthandler to dump all threads on, mid-playback, fatally.

Skipped wherever libmpv is absent, which includes CI — this is a dev-box and
release-machine gate, and the bundle smoke (``mpv_probe_main``, same option
builder) is what covers the shipped libraries.
"""

from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path

import pytest

from anki_miner.utils import mpv_loader

pytestmark = pytest.mark.skipif(not mpv_loader.mpv_available(), reason="needs a loadable libmpv")

# A loaded script logs under its own client name; nothing else uses these
# prefixes. auto_profiles/osc/ytdl_hook are off by default in libmpv but are
# listed so a default change cannot slip past.
_SCRIPT_PREFIXES = frozenset(
    {"stats", "console", "select", "positioning", "commands", "osc", "ytdl_hook", "auto_profiles"}
)
# On mpv <= 0.40 scripts load asynchronously after MPV() returns: read too
# early and an unfixed player looks clean. This settle window is load-bearing.
_SETTLE_S = 3.0

# --log-file line shape: "[  0.012][d][osc] text" -> capture the client name.
_LOG_PREFIX_RE = re.compile(r"^\[[^\]]*\]\[[^\]]*\]\[([^\]]+)\]")


def _prefixes_seen(**extra_options) -> set[str]:
    mpv_module = mpv_loader.load_mpv()
    mpv_loader._ensure_c_numeric()
    with tempfile.TemporaryDirectory() as td:
        log_path = Path(td) / "mpv.log"
        options = mpv_loader._player_options(mpv_module, video=False)
        # Detection reads --log-file, NOT a log_handler: mpv 0.41 loads the
        # builtin scripts synchronously inside init, before python-mpv's log
        # subscription is live, so a handler misses their lines on roughly
        # half the runs — which reads as a false "no scripts loaded". The log
        # file gets those early messages replayed from mpv's startup buffer,
        # so this channel is race-free (and it logs at debug on its own,
        # which the script clients need to be heard at all).
        options["log_file"] = str(log_path)
        options.update(extra_options)
        player = mpv_module.MPV(**options, ao="null")
        try:
            time.sleep(_SETTLE_S)
        finally:
            mpv_loader.terminate_mpv_player(player)
        text = log_path.read_text(encoding="utf-8", errors="replace")
    prefixes = {m.group(1) for line in text.splitlines() if (m := _LOG_PREFIX_RE.match(line))}
    return prefixes & _SCRIPT_PREFIXES


def test_python_mpv_exposes_probe_internals():
    """The option probe reaches into python-mpv (whose floor, mpv>=1.0.8, is
    open-ended). If these move, _builtin_script_options degrades silently to
    disabling nothing — this is what makes that visible."""
    mpv_module = mpv_loader.load_mpv()
    for name in ("_mpv_create", "_mpv_set_option_string", "_mpv_terminate_destroy"):
        assert callable(getattr(mpv_module, name, None)), name


def test_no_builtin_lua_script_loads_in_the_preview_core():
    assert _prefixes_seen() == set()


def test_control_the_same_core_does_load_scripts_without_the_options():
    """Negative control: without the disable options the very same core loads
    Lua, so the test above is measuring something.

    ``load_scripts=True`` is part of the re-enable set: on newer libmpv
    (observed on 0.41) ``--load-scripts=no`` gates the builtin scripts too —
    upstream closed the very gap Issue #112 worked around — so leaving the
    player's ``load_scripts=False`` in place would suppress the scripts this
    control exists to observe. On 0.35-0.40 the option only gated the user
    scripts directory, so re-enabling it there changes nothing.
    """
    supported = mpv_loader._builtin_script_options(mpv_loader.load_mpv())
    if not supported:
        pytest.skip("this libmpv accepted none of the script options")
    reenabled: dict[str, bool] = dict.fromkeys(supported, True)
    reenabled.pop("ytdl", None)  # ytdl_hook needs a URL to say anything
    reenabled["load_scripts"] = True
    assert _prefixes_seen(**reenabled) != set()
