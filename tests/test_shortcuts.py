"""Non-Steam games (Steam shortcuts), #161."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import optiscaler, shortcuts, xess  # noqa: E402


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
    return p


def _setup(tmp_path):
    home = tmp_path / "home"
    games = tmp_path / "games"
    sb = _touch(str(games / "Stellar Blade/SB/Binaries/Win64/SB-Win64-Shipping.exe"))
    os.makedirs(games / "Stellar Blade/Engine/Plugins", exist_ok=True)
    sifu = _touch(str(games / "Sifu/Sifu.exe"))
    icon = _touch(str(games / "Sifu/icon.png"), b"\x89PNG....")
    vdf = {"shortcuts": {
        "0": {"appid": -1234567890, "AppName": "Stellar Blade", "Exe": f'"{sb}"', "StartDir": f'"{os.path.dirname(sb)}"',
              "LaunchOptions": 'STEAM_COMPAT_DATA_PATH="/data/pfx/sb" %command%', "icon": ""},
        "1": {"appid": 287654321, "appname": "Sifu", "exe": f'"{sifu}"', "LaunchOptions": "", "icon": icon},
        "2": {"appid": 111, "AppName": "Moonlight", "Exe": '"/usr/bin/moonlight"', "LaunchOptions": ""},
    }}
    _touch(str(home / shortcuts.USERDATA / "12345/config/shortcuts.vdf"), b"\x00shortcuts\x00" + _kv(vdf["shortcuts"]) + b"\x08")
    return home, games


def test_shortcuts_keep_windows_games_with_their_top_folder_and_prefix(tmp_path):
    home, games = _setup(tmp_path)
    sc = {s["name"]: s for s in shortcuts.shortcuts(str(home))}
    assert set(sc) == {"Stellar Blade", "Sifu"}                       # Moonlight (not an .exe) left out
    sb = sc["Stellar Blade"]
    assert sb["appid"] == str(-1234567890 & 0xFFFFFFFF)               # Steam's unsigned 32-bit shortcut id
    assert sb["dir"] == str(games / "Stellar Blade")                   # out of Binaries/Win64 and the project folder
    assert sb["compat"] == "/data/pfx/sb"                              # its own prefix from the launch options
    assert sc["Sifu"]["compat"].endswith("compatdata/287654321") and sc["Sifu"]["dir"] == str(games / "Sifu")
    assert xess.prefix_dir(str(home), sb["appid"]) == "/data/pfx/sb/pfx/drive_c"
    assert xess.steam_install_dir(str(home), "287654321") == str(games / "Sifu")
    assert xess.steam_app_name(str(home), "287654321") == "Sifu"
    names = [g["name"] for g in xess.installed_games(str(home))]
    assert names == ["Sifu", "Stellar Blade"]
    assert shortcuts.icon_url(str(home), "287654321").startswith("data:image/png;base64,")
    assert shortcuts.icon_url(str(home), sb["appid"]) is None


def test_a_broken_shortcuts_file_is_skipped(tmp_path):
    home = tmp_path / "home"
    _touch(str(home / shortcuts.USERDATA / "1/config/shortcuts.vdf"), b"\x00shortcuts\x00\x05bad")
    assert shortcuts.shortcuts(str(home)) == []


def test_compat_path_forms():
    assert shortcuts.compat_path("STEAM_COMPAT_DATA_PATH=/a/b %command%") == "/a/b"
    assert shortcuts.compat_path("FOO=1 STEAM_COMPAT_DATA_PATH='/a b' %command%") == "/a b"
    assert shortcuts.compat_path("%command%") is None


def test_unreal_launcher_stub_is_not_where_optiscaler_goes(tmp_path):
    """#162 Gothic 1 Remake: a 310 KB G1R-Win64-Shipping.exe stub in the top folder, the 175 MB game in G1R/Binaries/Win64."""
    _touch(str(tmp_path / "G1R-Win64-Shipping.exe"), b"x" * 300)
    _touch(str(tmp_path / "G1R/Binaries/Win64/G1R-Win64-Shipping.exe"), b"x" * 5000)
    assert optiscaler.main_exe_dir(str(tmp_path)) == str(tmp_path / "G1R/Binaries/Win64")


def test_a_shortcut_s_own_prefix_wins_over_the_empty_compatdata_folder_steam_makes(tmp_path):
    """#166: Steam creates an empty compatdata/<appid> at a shortcut's first launch even though its launch options
    send Proton elsewhere (with ~ for the user's home); the real prefix must still win."""
    home, games = _setup(tmp_path)
    exe = _touch(str(games / "SB2/SB2.exe"))
    vdf = {"0": {"appid": 2972123661 - (1 << 32), "AppName": "Stellar Blade", "Exe": f'"{exe}"',
                 "LaunchOptions": "STEAM_COMPAT_DATA_PATH=~/Prefixes/stellar-blade %command%"},
           "1": {"appid": 222, "AppName": "Other", "Exe": f'"{exe}"', "LaunchOptions": "STEAM_COMPAT_DATA_PATH=${HOME}/p/o %command%"},
           "2": {"appid": 333, "AppName": "Plain", "Exe": f'"{exe}"', "LaunchOptions": "%command%"}}
    _touch(str(home / shortcuts.USERDATA / "12345/config/shortcuts.vdf"), b"\x00shortcuts\x00" + _kv(vdf) + b"\x08")
    os.makedirs(home / xess.COMPATDATA / "2972123661")                 # what Steam leaves: empty, no pfx/
    os.makedirs(home / xess.COMPATDATA / "333" / "pfx")
    assert xess.prefix_dir(str(home), "2972123661") == f"{home}/Prefixes/stellar-blade/pfx/drive_c"
    assert xess.prefix_dir(str(home), "222") == f"{home}/p/o/pfx/drive_c"
    assert xess.prefix_dir(str(home), "333") == str(home / xess.COMPATDATA / "333/pfx/drive_c")
    assert shortcuts.compat_path("STEAM_COMPAT_DATA_PATH=$HOME/x %command%", "/home/u/") == "/home/u/x"
    assert shortcuts.compat_path("STEAM_COMPAT_DATA_PATH=~user/x %command%", "/home/u") == "~user/x"
