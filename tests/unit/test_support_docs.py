from pathlib import Path

import yaml
from PyQt6.QtWidgets import QLabel, QLineEdit

ROOT = Path(__file__).resolve().parents[2]


def test_asr_less_frozen_install_guidance_offers_only_executable_remedies(qtbot, monkeypatch) -> None:
    from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel

    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    panel = SubtitlesSettingsPanel(suppress_optional_startup=True)
    qtbot.addWidget(panel)

    guidance = panel._asr_engine_guidance
    message = " ".join(label.text() for label in guidance.findChildren(QLabel))
    commands = [field.text() for field in guidance.findChildren(QLineEdit) if field.objectName() == "command-text"]
    assert "faster-whisper engine" in message
    assert "This packaged app cannot be extended" in message
    assert "launch the separate pipx-installed Anki Miner" in message
    assert "ASR-capable AppImage" in message
    assert commands == ['pipx install "anki-miner[asr]"']
    assert "pip install" not in message + " ".join(commands)

    installation = (ROOT / "README.md").read_text(encoding="utf-8").split("## Installation", maxsplit=1)[1]
    # The .deb ships the full bundle (ASR included) — the download table must
    # not resurrect the pre-v2.10 "excludes local Whisper" footnote for it.
    assert "| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |" in installation
    assert "² " not in installation
    # The Intel-mac footnote (the one remaining ASR-less artifact) keeps the
    # executable remedy the guidance block points at.
    assert 'pipx install "anki-miner[asr]"' in installation


def test_readme_exposes_first_install_recovery_and_troubleshooting() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    installation = readme.split("## Installation", maxsplit=1)[1].split("## Tabs", maxsplit=1)[0]
    visible_notes = installation.split("<details>", maxsplit=1)[0]

    heading = "### First-run notes (unsigned builds)"
    assert heading in visible_notes
    assert "**Windows SmartScreen**: **More info** -> **Run anyway**." in visible_notes
    assert "**Windows Defender false positive**" in visible_notes
    assert "report to Microsoft" in visible_notes

    troubleshooting = readme.split("## Troubleshooting", maxsplit=1)[1].split("## Roadmap", maxsplit=1)[0]
    for issue in (
        "Windows installer will not open / SmartScreen warning",
        "Fresh install has no definitions",
        "Add Dictionary stalls or fails",
        "Where are the logs?",
    ):
        assert f"| {issue}" in troubleshooting
    assert "[First-run notes](#first-run-notes-unsigned-builds)" in troubleshooting
    assert "Tools -> Setup Wizard or Tools -> Download Recommended Resources" in troubleshooting
    assert "keep the Yomitan ZIP intact (do not unzip it)" in troubleshooting
    assert "last visible stage" in troubleshooting
    assert "dictionary ZIP name, source, and size" in troubleshooting
    assert "%USERPROFILE%\\.anki_miner\\anki_miner.log" in troubleshooting
    assert "~/.anki_miner/anki_miner.log" in troubleshooting
    assert "`.1` through `.5` suffixes" in troubleshooting
    assert "Help → Export Diagnostics…" in troubleshooting
    assert (
        "Review it before uploading because it contains file paths and file names from your computer" in troubleshooting
    )
    assert "`ANKI_MINER_LOG_LEVEL=DEBUG`" in troubleshooting


def test_bug_report_collects_log_files_and_status() -> None:
    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml").read_text(encoding="utf-8"))
    fields = {field["id"]: field for field in form["body"] if "id" in field}

    upload = fields["log_files"]
    assert upload["type"] == "upload"
    assert upload["attributes"]["label"] == "Anki Miner log files"
    upload_help = upload["attributes"]["description"]
    assert "%USERPROFILE%\\.anki_miner\\anki_miner.log" in upload_help
    assert "Rotated logs use `.1` through `.5`" in upload_help
    assert "ZIP the `anki_miner.log*` files" in upload_help
    assert "Help → Open Log Folder" in upload_help
    assert "Help → Export Diagnostics…" in upload_help
    assert "Review logs for private information before uploading" in upload_help
    assert upload["validations"] == {"required": False, "accept": ".log,.txt,.zip"}

    status = fields["log_status"]
    assert status["type"] == "dropdown"
    assert status["attributes"]["label"] == "Log status"
    assert status["attributes"]["options"] == [
        "Attached above",
        "No log file was created",
        "Could not access the log folder",
    ]
    assert status["validations"]["required"] is True

    version_help = fields["version"]["attributes"]["description"]
    assert "use the version from the installer filename or the release page" in version_help
    assert "optional paste alternative" in fields["logs"]["attributes"]["label"]
    assert fields["logs"]["attributes"]["render"] == "shell"
    assert fields["logs"]["validations"]["required"] is False
