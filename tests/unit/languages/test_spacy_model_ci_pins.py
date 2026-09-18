"""CI's real-model tests get the same model bytes the packs pin (and the CI step pre-lands every one)."""

from __future__ import annotations

import re
from pathlib import Path

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.services.language_pack_installer import load_pack
from tests.unit.languages.test_spacy_runtime_pack import SPACY_MODEL_PACKAGES

CI = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PIN = re.compile(
    r'"(?P<pkg>[a-z]{2}_core_(?:web|news)_sm) @ (?P<url>https://github\.com/explosion/spacy-models/releases/download/\S+?\.whl)#sha256=(?P<sha>[0-9a-f]{64})"'
)


#: A model whose release filename is no valid wheel name (hu: ``any`` in the version slot) cannot be a
#: ``name @ url`` requirement: the step downloads it, checks the pinned sha256, and installs it under a valid name.
DOWNLOAD_PIN = re.compile(
    r'curl -fsSL -o "\$RUNNER_TEMP/(?P<wheel>(?P<pkg>[a-z]{2}_core_news_(?:sm|md))-[0-9.]+-py3-none-any\.whl)" '
    r"(?P<url>https://huggingface\.co/\S+?\.whl)\n"
    r'\s+echo "(?P<sha>[0-9a-f]{64})  \$RUNNER_TEMP/(?P=wheel)" \| sha256sum -c -\n'
    r'\s+pip install "\$RUNNER_TEMP/(?P=wheel)"'
)


def _pins() -> dict[str, tuple[str, str]]:
    pins = {m["pkg"]: (m["url"], m["sha"]) for m in PIN.finditer(CI)}
    pins.update({m["pkg"]: (m["url"], m["sha"]) for m in DOWNLOAD_PIN.finditer(CI)})
    return pins


def test_the_test_job_installs_every_western_model():
    assert set(_pins()) == set(SPACY_MODEL_PACKAGES)
    test_job = CI.split("\n  test:", 1)[1].split("\n  wheel-assets:", 1)[0]
    assert all(f'"{pkg} @ ' in test_job or f"$RUNNER_TEMP/{pkg}-" in test_job for pkg in SPACY_MODEL_PACKAGES)
    assert test_job.index('pip install -e ".[dev,languages]"') < test_job.index("_core_")
    assert test_job.rindex("_core_") < test_job.index("pytest -m")


def test_every_model_pack_matches_its_ci_pin():
    pins = _pins()
    checked = 0
    for code in AVAILABLE_LANGUAGES:
        pack = load_pack(code)
        if pack is None or pack.requires != ("_spacy",):
            continue
        (component,) = pack.components
        assert component.universal is not None
        assert pins[component.import_name] == (component.universal.url, component.universal.sha256)
        checked += 1
    assert checked >= 1
