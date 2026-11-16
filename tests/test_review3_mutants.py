"""Review 3: tests that kill mutants the cloud run called equivalent (work/ci-checks/mutants.json)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
from test_governor import Game  # noqa: E402
from clawdeck import governor as gov  # noqa: E402


def test_r3_kill_g_check_binds_off_a_lighter_scene_that_frees_the_limit_gets_no_look_at_the_full_limit():
    """Most frames, a game that draws the whole limit. A minute after the last look at the full limit the scene gets a
    little lighter: 4 % more frames and the package 3 W under the limit. The limit holds nothing back now (the pace is
    the reference within a few looks): no look at the full limit. Without `binds` in the rule, the "moved 3 %, a
    minute since the last look" clause fires in the looks before the free reference is taken."""
    lift = {"x": 1.0, "free": False}
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w, t: lift["x"] * (60.0 if w >= 26 else 60.0 - (26 - w)))
    g.draw = lambda w, t: float(w) - (3.0 if lift["free"] else 0.0)
    g.run(40)
    g.run(max(0.0, g.st.ref_at + 62 - g.t))
    assert g.st.mode == "hold" and g.limit < 26
    lift["x"], lift["free"], t0 = 1.04, True, g.t
    g.run(20)
    looks = [(t, n) for t, n in g.notes if t >= t0 and "checking what the full" in n]
    assert looks == [], looks


import pytest  # noqa: E402
from test_backend import fake_hw, _mango_rows  # noqa: E402,F401
from clawdeck import autotune  # noqa: E402


def test_r3_kill_w_two_sources_a_mangoapp_gap_of_seconds_is_not_filled_from_the_other_log(fake_hw, tmp_path, monkeypatch):
    """mangoapp gives rows, then none for 5 s (under MANGOAPP_RELOG_S) while the game's own MangoHud log writes a row a
    second. The original gives no frames for those looks; without the "between two rows" return the own log's rows
    come back as frames from the second silent look on."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: True)
    logs = tmp_path / "mangohud"
    logs.mkdir()
    own = logs / "game_2026-11-15_14-00-00.csv"
    own.write_text("os\nLinux\nfps,frametime\n")
    live = tmp_path / "mangoapp_2026-11-15_14-00-01.csv"
    _mango_rows(live, [16.7] * 3, header=True)
    smp = autotune.Sampler(fake_hw, str(logs), home=str(tmp_path))
    smp.set_prefix(None)
    smp.frametime()
    _mango_rows(live, [16.7] * 30)
    clock["t"] += 0.5
    assert smp.frametime()[1] == pytest.approx(59.9, abs=0.1)
    got = []
    for i in range(10):                                   # 5 s: mangoapp silent, the own log a row a second
        if i % 2 == 0:
            with open(own, "a") as f:
                f.write("60.0,16.7\n")
        clock["t"] += 0.5
        smp._log_checked = -1e9
        got.append(smp.frametime())
    assert all(x == (None, None) for x in got), got
