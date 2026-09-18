"""Every license the frozen bundle owes a user is actually collected into it.

Anki Miner is GPL-3.0-or-later, so a binary artifact has to carry the app's own
LICENSE (GPLv3 section 4) on top of the third-party notices under ``licenses/``.
It carried neither the app's LICENSE nor ``licenses/yomitan/`` until this module
existed: the yomitan directory sat on disk with no ``*_license_datas`` variable
and no term in the ``datas=`` sum, which is invisible to every other gate —
``black`` skips ``.spec`` files and the spec is never executed by the test suite.

Spec TEXT is parsed rather than executed (PyInstaller is a build-time tool and is
not installed in this venv), the same convention as ``tests/unit/test_spec_hygiene.py``.

Assertions are on the **sum term** (``+ <name>_license_datas``), never on the
declaration alone: a declared-but-unsummed variable is exactly the failure being
regressed, and ``assert "<name>_license_datas" in spec`` would pass through it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "anki_miner.spec"
LICENSES_DIR = ROOT / "licenses"

#: ``<stem>_license_dir = os.path.join(project_root, "licenses", "<dirname>")``.
_LICENSE_DIR_DECL = re.compile(
    r'(?P<stem>\w+)_dir = os\.path\.join\(project_root, "licenses", "(?P<dirname>[\w.-]+)"\)'
)


def _datas_sum() -> str:
    """Return the ``datas=`` argument's ``+ <name>`` sum, list body excluded.

    Slicing from the list's closing ``]`` to the next Analysis keyword keeps a
    term that only appears inside a comment or a declaration out of the match.
    """
    text = SPEC.read_text(encoding="utf-8")
    _before, marker, rest = text.partition("    datas=[")
    assert marker, "anki_miner.spec has no datas=[ argument"
    _list_body, closer, after = rest.partition("\n    ]\n")
    assert closer, "anki_miner.spec's datas=[ list is unterminated"
    sum_body, terminator, _tail = after.partition("\n    hiddenimports=[")
    assert terminator, "anki_miner.spec's datas= sum does not reach hiddenimports="
    return sum_body


def test_the_bundle_collects_the_apps_own_license() -> None:
    """The GPLv3 text lands at the _MEIPASS root, beside the licenses/ tree.

    ``.`` is as close to the executable as ``datas`` can reach: PyInstaller
    rejects a DEST_DIR outside the top-level directory, and COLLECT prefixes the
    contents directory onto every non-EXECUTABLE entry.
    """
    spec = SPEC.read_text(encoding="utf-8")
    assert 'app_license_file = os.path.join(project_root, "LICENSE")' in spec
    assert 'app_license_datas.append((app_license_file, "."))' in spec
    assert "+ app_license_datas" in _datas_sum()
    assert (ROOT / "LICENSE").is_file()


def test_the_bundle_collects_the_yomitan_notice() -> None:
    """GPLv3 code ported into services/deinflection.py travels with its notice."""
    spec = SPEC.read_text(encoding="utf-8")
    assert 'yomitan_license_dir = os.path.join(project_root, "licenses", "yomitan")' in spec
    assert '(yomitan_license_dir, os.path.join("licenses", "yomitan"))' in spec
    assert "+ yomitan_license_datas" in _datas_sum()
    assert (LICENSES_DIR / "yomitan" / "COPYING.GPLv3").is_file()


def test_every_committed_license_directory_reaches_the_datas_sum() -> None:
    """A licences/ directory nobody wired into the sum ships nowhere.

    That is how licenses/yomitan/ stayed out of 23 shipped notice directories.
    """
    spec = SPEC.read_text(encoding="utf-8")
    declared = {m.group("dirname"): m.group("stem") for m in _LICENSE_DIR_DECL.finditer(spec)}
    on_disk = {path.name for path in LICENSES_DIR.iterdir() if path.is_dir()}

    assert on_disk <= set(declared), f"licenses/ dirs with no spec declaration: {sorted(on_disk - set(declared))}"

    sum_body = _datas_sum()
    unsummed = sorted(name for name in on_disk if f"+ {declared[name]}_datas" not in sum_body)
    assert not unsummed, f"declared but never added to datas=: {unsummed}"
