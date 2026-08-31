"""Registry auto-discovery: the ``_BUILDERS`` registration loop itself.

Stage 1 hand-wrote three near-identical builders (``_ja_builder``/
``_zh_builder``/``_ko_builder``), one per ``AVAILABLE_LANGUAGES`` entry. This
suite pins the loop that replaced them: registration is gated by
``importlib.util.find_spec`` at REGISTRATION time (never inside the lazy
builder), so a whitelisted code with no package on disk never enters
``_BUILDERS`` — which is what lets ``config_language``'s
``language not in _BUILDERS`` membership check degrade it to ``ja``. The loop
itself must also import nothing: a ja-only session must never pay for (or
fail on) the zh/ko engines' optional dependency sets.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from anki_miner.languages import AVAILABLE_LANGUAGES, registry


def test_a_packageless_whitelisted_code_never_registers(monkeypatch):
    """The find_spec gate runs at registration time, not inside the builder."""
    monkeypatch.setattr(registry, "AVAILABLE_LANGUAGES", ("xx-no-such-package",))

    registry._discover()

    assert "xx-no-such-package" not in registry._BUILDERS


def test_a_packageless_code_the_gate_skipped_degrades_to_ja(monkeypatch):
    """The registration gate and config_language's membership check agree."""
    monkeypatch.setattr(registry, "AVAILABLE_LANGUAGES", ("xx-no-such-package",))
    registry._discover()

    config = SimpleNamespace(language="xx-no-such-package")

    assert registry.config_language(config) == "ja"


def test_rerunning_discovery_does_not_disturb_already_registered_codes(monkeypatch):
    """Re-running the loop (as the fixture above does) is a no-op for real codes."""
    monkeypatch.setattr(registry, "AVAILABLE_LANGUAGES", ("xx-no-such-package",))
    before = dict(registry._BUILDERS)

    registry._discover()

    assert dict(registry._BUILDERS) == before


def test_every_available_language_registers():
    assert set(registry.available_languages()) >= set(AVAILABLE_LANGUAGES)


def test_registering_the_builders_imports_no_language_package():
    """Fresh-process import of the registry must not execute ja/zh/ko's __init__.

    A subprocess is required: this test's own process already has other
    languages' packages loaded by earlier tests, so checking ``sys.modules``
    in-process would pass or fail depending on test order rather than on
    what module import actually did.
    """
    src = (
        "import sys\n"
        "import anki_miner.languages.registry\n"
        "loaded = [m for m in sys.modules "
        "if m.startswith('anki_miner.languages.') "
        "and m.rsplit('.', 1)[-1] in ('ja', 'zh', 'ko')]\n"
        "print(sorted(loaded))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "[]", out.stdout
