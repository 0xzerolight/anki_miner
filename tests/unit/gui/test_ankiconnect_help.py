"""AnkiConnect install help and the Anki launcher shared by the wizard and System Health (B09, D11)."""

from __future__ import annotations

from anki_miner.gui.utils import ankiconnect_help as help_mod


def test_the_three_steps_carry_the_addon_code():
    steps = help_mod.ankiconnect_install_steps()

    assert steps[0] == "Open Anki."
    assert steps[1] == ("In Anki choose Tools → Add-ons → Get Add-ons…, paste the code 2055492159, and click OK.")
    assert steps[2] == "Restart Anki."
    assert help_mod.ANKICONNECT_ADDON_CODE == "2055492159"


def test_the_help_block_numbers_the_steps():
    lines = help_mod.ankiconnect_install_help().splitlines()

    assert len(lines) == 3
    assert [line[:3] for line in lines] == ["1. ", "2. ", "3. "]
    assert "2055492159" in lines[1]


def test_windows_finds_anki_in_its_standard_place(tmp_path):
    exe = tmp_path / "Programs" / "Anki" / "anki.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    command = help_mod.anki_launch_command(platform="win32", environ={"LOCALAPPDATA": str(tmp_path)})

    assert command == [str(exe)]


def test_windows_without_the_install_has_no_command(tmp_path):
    assert help_mod.anki_launch_command(platform="win32", environ={"LOCALAPPDATA": str(tmp_path)}) is None
    assert help_mod.anki_launch_command(platform="win32", environ={}) is None


def test_macos_opens_the_app_bundle(tmp_path):
    bundle = tmp_path / "Anki.app"
    bundle.mkdir()

    assert help_mod.anki_launch_command(platform="darwin", environ={}, mac_bundle=bundle) == ["open", str(bundle)]
    assert help_mod.anki_launch_command(platform="darwin", environ={}, mac_bundle=tmp_path / "Nope.app") is None


def test_linux_uses_anki_on_path(monkeypatch):
    monkeypatch.setattr(help_mod.shutil, "which", lambda name: "/usr/bin/anki" if name == "anki" else None)

    assert help_mod.anki_launch_command(platform="linux", environ={}) == ["/usr/bin/anki"]


def test_linux_without_anki_on_path_has_no_command(monkeypatch):
    monkeypatch.setattr(help_mod.shutil, "which", lambda name: None)

    assert help_mod.anki_launch_command(platform="linux", environ={}) is None


def test_launch_starts_the_command_detached(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    class FakeProcess:
        @staticmethod
        def startDetached(program, arguments):  # noqa: N802 - mirrors QProcess
            calls.append((program, list(arguments)))
            return True, 42

    monkeypatch.setattr(help_mod, "QProcess", FakeProcess)

    assert help_mod.launch_anki(["open", "/Applications/Anki.app"]) is True
    assert calls == [("open", ["/Applications/Anki.app"])]
