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


def test_contributing_records_the_logging_contract() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "## Logging" in contributing
    logging_section = contributing.split("## Logging", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    # The failure-record rule: operation, subject, exception type AND message.
    assert "`type(exc).__name__`" in logging_section
    assert "`str(exc)`" in logging_section
    assert "exc_info" in logging_section

    # The choke points a new call site is supposed to reuse instead of inventing one.
    for choke_point in (
        "log_start",
        "report_failure",
        "log_end",
        "timed_phase",
        "log_summary",
        "run_supervised",
        "log_command",
        "write_diagnostics_bundle",
        "LogWidget",
        "ScreenIssueBanner",
        "GUIPresenter",
        "TaskRegistry",
    ):
        assert choke_point in logging_section, choke_point

    # `suppressed()` is the only sanctioned broad swallow, and the ratchet that
    # keeps new ones out is named so the failure message can be acted on.
    assert "suppressed" in logging_section
    assert "silent_except_budget.txt" in logging_section

    # Levels and the on-disk contract.
    assert "`ANKI_MINER_LOG_LEVEL`" in logging_section
    assert "capped" in logging_section
    # No config toggles: the locked decision, stated where a contributor reads it.
    assert "no new config" in logging_section.lower()

    # The grep-anchor table has to be here for the anchors to be greppable.
    for anchor in ("Run start:", "Task start:", "Session end:", "Pipeline start:"):
        assert anchor in logging_section, anchor


def test_architecture_describes_the_real_crash_and_early_sinks() -> None:
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    # The old row claimed the crash file is an early-boot sink. It is not: it is
    # faulthandler's native-crash sink, folded into the log at the next start.
    assert "captures a failure early enough that logging is not up yet" not in architecture
    assert "_fold_previous_crash" in architecture
    assert "SIGUSR1" in architecture
    assert "AnkiMiner-early-crash.log" in architecture
    assert "anki_miner.child.log" in architecture

    # Watchdog paragraph names the pause spans that turn a suppressed freeze
    # into a record instead of silence.
    assert "paused_stall_detection" in architecture
    assert "stall detection resumed" in architecture


def test_readme_names_every_file_a_reporter_has_to_send() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    troubleshooting = readme.split("## Troubleshooting", maxsplit=1)[1].split("## Roadmap", maxsplit=1)[0]

    assert "anki_miner.crash" in troubleshooting
    assert "anki_miner.child.log" in troubleshooting
    # The privacy warning stays honest only if the bundle's contents are listed.
    assert "settings.json" in troubleshooting
    assert "environment.txt" in troubleshooting
    for member in ("resources.txt", "stores.txt", "disk.txt", "screens.txt", "health.txt"):
        assert member in troubleshooting, member
    assert "queue snapshots" in troubleshooting


def test_changelog_unreleased_records_the_logging_overhaul() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", maxsplit=1)[1].split("\n## [", maxsplit=1)[0]

    changed = unreleased.split("### Changed", maxsplit=1)[1].split("### Removed", maxsplit=1)[0]
    assert "16 MiB" in changed
    assert "threadName" in changed or "thread name" in changed
    assert "bundle_format" in changed or "bundle format" in changed

    fixed = unreleased.split("### Fixed", maxsplit=1)[1]
    assert "audio" in fixed.lower()
    assert "video id" in fixed.lower() or "video ids" in fixed.lower()
