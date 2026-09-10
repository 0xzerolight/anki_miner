"""Spec hygiene: the Linux Vulkan ICD loader and tkinter never freeze in.

Spec TEXT is parsed rather than executed — PyInstaller is a build-time tool
and is not installed in this venv — the same convention as
``tests/unit/languages/test_zh_bundling.py``.

Two independent filters are exercised:

1. ``_HOST_ONLY_LIB_RE``, a post-Analysis regex filter over ``a.binaries``.
   It already dropped host-audio client libs (libasound/libpulse/jack/
   pipewire) bindepend pulls in via the vendored libmpv; this item extends it
   to also drop the Vulkan ICD loader (libvulkan.so.1) that bindepend pulls
   in via libggml-vulkan's NEEDED entry. A frozen copy would shadow the host
   GPU driver's own loader — see ``PyInstaller-Hooks/hook-pywhispercpp.py``'s
   docstring, which filters the SAME loader out of the hook's own explicitly
   collected binaries list (a disjoint set from ``a.binaries``, which is why
   both filters are needed).
2. ``excludes``, gaining ``tkinter``/``_tkinter`` — nothing in the app imports
   either, but stdlib hooks pull them in unless excluded.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC = PROJECT_ROOT / "anki_miner.spec"


def _list_body(name: str) -> str:
    """Return the body of the spec's ``<name>=[ ... ]`` Analysis argument."""
    text = SPEC.read_text(encoding="utf-8")
    _before, marker, rest = text.partition(f"{name}=[")
    assert marker, f"anki_miner.spec has no {name}=[ list"
    body, closer, _after = rest.partition("\n    ],")
    assert closer, f"anki_miner.spec's {name}=[ list is unterminated"
    return body


def _host_only_lib_regex() -> re.Pattern[str]:
    """Lift the literal ``_HOST_ONLY_LIB_RE`` pattern out of the spec source
    and compile it directly — no need to execute the spec for a regex
    literal. Tolerates black wrapping the assignment onto its own line
    (``re.compile(\\n    r"...")``) as well as a single-line form."""
    text = SPEC.read_text(encoding="utf-8")
    match = re.search(r'_HOST_ONLY_LIB_RE = re\.compile\(\s*r"(.+?)"\s*\)', text, re.DOTALL)
    assert match, 'anki_miner.spec has no _HOST_ONLY_LIB_RE = re.compile(r"...") literal'
    return re.compile(match.group(1))


def test_the_linux_vulkan_loader_is_dropped_from_binaries() -> None:
    regex = _host_only_lib_regex()
    for name in ("libvulkan.so.1", "libvulkan.so.1.3.239", "libvulkan.so"):
        assert regex.match(name), f"_HOST_ONLY_LIB_RE does not drop {name}"


def test_windows_and_macos_vulkan_carriers_are_untouched() -> None:
    """The regex is .so-suffix-scoped: it must never reach for vulkan-1.dll
    (Windows, deliberately shipped for libmpv) or a macOS .dylib."""
    regex = _host_only_lib_regex()
    for name in ("vulkan-1.dll", "libvulkan.dylib", "libMoltenVK.dylib"):
        assert not regex.match(name), f"_HOST_ONLY_LIB_RE wrongly matches {name}"


def test_unrelated_libs_still_pass_through() -> None:
    """A regression guard: the audio-lib half of the filter must keep working
    and the new alternative must not become an accidental catch-all."""
    regex = _host_only_lib_regex()
    for name in ("libasound.so.2", "libssl.so.3", "libggml-vulkan.so", "libvulkan_radeon.so"):
        matched = bool(regex.match(name))
        expected = name == "libasound.so.2"
        assert matched == expected, f"_HOST_ONLY_LIB_RE match({name}) = {matched}, expected {expected}"


def test_tkinter_is_excluded_from_the_graph() -> None:
    excludes = _list_body("excludes")
    for engine in ("tkinter", "_tkinter"):
        assert f'"{engine}",' in excludes, f"anki_miner.spec does not exclude {engine}"
