"""Analytics milestones state facts, not invented rank titles (decision D47).

The milestone rows used to carry gamified names ("First Steps", "Master Miner",
"Series Connoisseur") minted as plain Python strings inside
``services/stats_service.py`` — outside every ``tr()`` seam, so a German user
read them in English on an otherwise German tab. The service now yields only a
stable :class:`MilestoneKind` plus the numbers; the Analytics tab owns the
wording and routes it through ``tr()``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QLabel, QProgressBar

from anki_miner.gui import i18n
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import Milestone, MilestoneKind

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"

#: Every source string the milestone rows can emit. Must stay in sync with
#: ``AnalyticsTab._milestone_text``.
MILESTONE_SOURCES = [
    "%1 cards created",
    "%1 mining sessions completed",
    "%1 series mined",
]


@pytest.fixture
def tab(qtbot) -> AnalyticsTab:
    service = MagicMock()
    service.is_available.return_value = False  # no refresh; we drive rendering directly
    widget = AnalyticsTab(service)
    qtbot.addWidget(widget)
    return widget


@pytest.mark.parametrize(
    ("kind", "threshold", "expected"),
    [
        (MilestoneKind.CARDS, 50, "50 cards created"),
        (MilestoneKind.CARDS, 10000, "10,000 cards created"),
        (MilestoneKind.SESSIONS, 100, "100 mining sessions completed"),
        (MilestoneKind.SERIES, 25, "25 series mined"),
    ],
)
def test_milestone_text_states_a_fact(tab: AnalyticsTab, kind, threshold, expected) -> None:
    """No rank titles: the label is the threshold restated as a plain fact."""
    milestone = Milestone(kind=kind, threshold=threshold, current_value=0, achieved=False)
    assert tab._milestone_text(milestone) == expected


def test_milestone_row_shows_the_fact_and_its_progress(tab: AnalyticsTab) -> None:
    """The row renders one factual label plus the unchanged progress readout."""
    milestone = Milestone(
        kind=MilestoneKind.SESSIONS,
        threshold=25,
        current_value=7,
        achieved=False,
    )

    row = tab._create_milestone_widget(milestone)

    labels = [w.text() for w in row.findChildren(QLabel)]
    assert labels == ["25 mining sessions completed"]

    bar = row.findChild(QProgressBar)
    assert bar is not None
    assert bar.maximum() == 25
    assert bar.value() == 7
    assert bar.format() == "7/25"


def test_update_milestones_renders_one_row_per_milestone(tab: AnalyticsTab) -> None:
    tab._update_milestones(
        [
            Milestone(kind=MilestoneKind.CARDS, threshold=50, current_value=50, achieved=True),
            Milestone(kind=MilestoneKind.SERIES, threshold=3, current_value=1, achieved=False),
        ]
    )
    rendered = []
    for i in range(tab.milestones_layout.count()):
        item = tab.milestones_layout.itemAt(i)
        widget = item.widget() if item is not None else None
        assert widget is not None
        rendered.extend(label.text() for label in widget.findChildren(QLabel))
    assert rendered == ["50 cards created", "3 series mined"]


def test_every_milestone_source_is_extracted_for_translation() -> None:
    """The wording lives in the GUI layer, so it must reach the catalogs."""
    root = ET.parse(TS_DIR / "anki_miner_en.ts").getroot()
    sources: set[str] = set()
    for context in root.findall("context"):
        name = context.find("name")
        if name is None or name.text != "AnalyticsTab":
            continue
        sources.update((m.find("source").text or "") for m in context.findall("message"))
    assert set(MILESTONE_SOURCES) <= sources


def test_german_catalog_translates_every_milestone_source() -> None:
    """The bug this task closes: a German user must not read English here."""
    root = ET.parse(TS_DIR / "anki_miner_de.ts").getroot()
    translations: dict[str, str] = {}
    for context in root.findall("context"):
        name = context.find("name")
        if name is None or name.text != "AnalyticsTab":
            continue
        for message in context.findall("message"):
            translation = message.find("translation")
            if translation is None or translation.get("type") == "unfinished":
                continue
            translations[message.find("source").text or ""] = (translation.text or "").strip()
    for source in MILESTONE_SOURCES:
        assert translations.get(source), f"German catalog leaves {source!r} untranslated"


def test_milestone_text_follows_the_active_translator(tab: AnalyticsTab, qapp) -> None:
    """End-to-end proof the rendered row changes language with the UI."""
    english = tab._milestone_text(Milestone(kind=MilestoneKind.SERIES, threshold=25))
    translators = i18n.install_translators(qapp, "de")
    try:
        german = tab._milestone_text(Milestone(kind=MilestoneKind.SERIES, threshold=25))
    finally:
        for translator in translators:
            qapp.removeTranslator(translator)
    assert german != english
    assert "25" in german
