"""Emulator frontends (ES-DE): which game runs, so each one gets its own power and Autotune."""
import asyncio
import os
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import frontend, shortcuts  # noqa: E402

ESDE = 3119184808                      # the user's ES-DE shortcut (2026-11-14)
SETTINGS = b'<?xml version="1.0"?>\n<bool name="CustomEventScripts" value="false" />\n<string name="UIMode" value="full" />\n'


def _kv(d: dict) -> bytes:
    out = b""
    for k, v in d.items():
        if isinstance(v, dict):
            out += b"\x00" + k.encode() + b"\x00" + _kv(v)
        elif isinstance(v, int):
            out += b"\x02" + k.encode() + b"\x00" + struct.pack("<i", v)
        else:
            out += b"\x01" + k.encode() + b"\x00" + v.encode() + b"\x00"
    return out + b"\x08"


def _touch(p, data=b"x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def _home(tmp_path, opts=""):
    home = tmp_path / "home"
    vdf = {"0": {"appid": ESDE - 2 ** 32, "AppName": "EmulationStationDE",
                 "Exe": '"/home/u/Emulation/tools/launchers/es-de/es-de.sh"', "LaunchOptions": opts},
           "1": {"appid": 12345, "AppName": "Blur", "Exe": '"/home/u/Games/Blur/Blur.exe"', "LaunchOptions": ""}}
    _touch(str(home / shortcuts.USERDATA / "1/config/shortcuts.vdf"), b"\x00shortcuts\x00" + _kv(vdf) + b"\x08")
    _touch(str(home / "ES-DE/settings/es_settings.xml"), SETTINGS)
    return str(home)


def test_es_de_scripts_write_the_game_the_plugin_reads(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(frontend, "_esde_running", lambda: False)
    r = frontend.esde_install(home)
    assert r["ok"] and r["scripts"] and r["setting"] and r["appid"] == str(ESDE) and not r["wrapper"]
    with open(os.path.join(home, "ES-DE/settings/es_settings.xml"), encoding="utf-8") as f:
        xml = f.read()
    assert '<bool name="CustomEventScripts" value="true" />' in xml and 'name="UIMode" value="full"' in xml
    # ES-DE runs every script in the event's folder with ROM path, game name, system name, system full name
    for ev, args in (("game-start", ["/roms/ps3/Demon's Souls.iso", "Demon's Souls", "ps3", "Sony PlayStation 3"]),):
        subprocess.run([os.path.join(home, "ES-DE/scripts", ev, "clawdeck.sh"), *args], env={"HOME": home, "PATH": "/bin:/usr/bin"},
                       check=True)
    ev = frontend.read(home)
    assert ev == {"event": "start", "system": "ps3", "rom": "/roms/ps3/Demon's Souls.iso", "name": "Demon's Souls"}
    assert frontend.key(str(ESDE), ev) == f"{ESDE}#ps3/Demon's Souls"
    subprocess.run([os.path.join(home, "ES-DE/scripts/game-end/clawdeck.sh"), "/roms/ps3/Demon's Souls.iso", "Demon's Souls",
                    "ps3", "Sony PlayStation 3"], env={"HOME": home, "PATH": "/bin:/usr/bin"}, check=True)
    assert frontend.read(home)["event"] == "end"


def test_es_de_settings_are_left_alone_while_it_runs(tmp_path, monkeypatch):
    """ES-DE writes its settings when it quits: a change made while it runs would be lost."""
    home = _home(tmp_path)
    monkeypatch.setattr(frontend, "_esde_running", lambda: True)
    r = frontend.esde_install(home)
    assert not r["ok"] and "close ES-DE" in r["error"]
    with open(os.path.join(home, "ES-DE/settings/es_settings.xml"), "rb") as f:
        assert f.read() == SETTINGS
    assert not os.path.exists(os.path.join(home, "ES-DE/scripts"))


def test_es_de_is_set_up_with_the_wrapper_in_its_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend, "_esde_running", lambda: False)
    home = _home(tmp_path, opts="/home/u/.local/share/clawdeck/clawdeck-launch.sh %command%")
    assert not frontend.esde_status(home)["ok"]
    r = frontend.esde_install(home)
    assert r["ok"] and r["wrapper"] and frontend.esde_status(home)["ok"]
    assert not frontend.esde_status(str(tmp_path / "nobody"))["present"]


def test_es_de_per_game_comes_out_again(tmp_path, monkeypatch):
    """Remove takes out ClawDeck's scripts only: ES-DE's setting and the launch wrapper stay."""
    monkeypatch.setattr(frontend, "_esde_running", lambda: False)
    home = _home(tmp_path, opts="/home/u/.local/share/clawdeck/clawdeck-launch.sh %command%")
    frontend.esde_install(home)
    r = frontend.esde_remove(home)
    assert r["ok"] and not r["scripts"] and r["setting"] and r["wrapper"] and not frontend.esde_status(home)["ok"]
    assert frontend.esde_remove(home)["ok"]                      # nothing left to remove is fine


def test_a_torn_or_foreign_event_file_is_no_event(tmp_path):
    home = str(tmp_path)
    assert frontend.read(home) is None and frontend.stamp(home) is None
    _touch(frontend.event_path(home), b"start\nps3\n")
    assert frontend.read(home) is None
    _touch(frontend.event_path(home), b"launch\nps3\n/r.iso\nR\n")
    assert frontend.read(home) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="inotify is Linux's")
def test_the_watch_wakes_only_when_the_script_writes(tmp_path, monkeypatch):
    """No timer: the plugin's loop wakes when ES-DE's script renames the event into place, not for other files."""
    home = _home(tmp_path)
    monkeypatch.setattr(frontend, "_esde_running", lambda: False)
    frontend.esde_install(home)
    os.makedirs(os.path.dirname(frontend.event_path(home)), exist_ok=True)
    seen = []

    async def go():
        w = frontend.Watch(home, lambda: seen.append(frontend.read(home)["event"]))
        _touch(os.path.join(os.path.dirname(frontend.event_path(home)), "other"))
        await asyncio.sleep(0.2)
        subprocess.run([os.path.join(home, "ES-DE/scripts/game-start/clawdeck.sh"), "/r/nes/M.nes", "M", "nes", "NES"],
                       env={"HOME": home, "PATH": "/bin:/usr/bin"}, check=True)
        await asyncio.sleep(0.2)
        w.close()
    asyncio.run(go())
    assert seen == ["start"]
