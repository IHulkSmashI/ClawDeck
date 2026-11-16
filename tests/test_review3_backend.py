"""Review 3: failing tests against the wiring (main.py / autotune.py). Each test states the behaviour the user asked for;
a failure is the finding."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
from test_backend import fake_hw, plugin, _frontend_event, _Game, _w  # noqa: E402,F401
from clawdeck import autotune, governor, power, profiles, sysfs  # noqa: E402


def test_r3_a_frontend_games_limit_does_not_change_because_autotune_saved_learning(plugin):
    """F: ES-DE's own pick is 30 W; the last pick on battery (another game) is 10 W. A game ES-DE starts, never picked,
    works under ES-DE's 30 W. After one session Autotune saved `learned` under the game's key; the next session must
    still work under ES-DE's 30 W: learning is not a pick."""
    p = plugin
    esde, game = "3119184808", "3119184808#ps2/GT4"
    p.run(p.game_started("42"))
    p.run(p.set_tdp(10))                       # a pick in another game: last_profile_battery = custom 10 W
    p.run(p.game_stopped("42"))
    p.profiles.set(esde, False, {"base": "custom", "tdp": 30})     # ES-DE's own pick
    p.run(p.game_started(esde))
    assert _frontend_event(p, "start", "ps2", "/roms/ps2/GT4.iso", "GT4")
    s = p.autotune.session
    assert s.appid == game and s.ceiling_tdp == 30                 # first session: ES-DE's pick
    s.learned.start, s.settled = 20, True                          # a session that held 20 W
    assert _frontend_event(p, "end", "ps2", "/roms/ps2/GT4.iso", "GT4")
    own = p.profiles.own(game, False)
    assert "learned" in own and not own.get("base")                # learning only, no pick
    assert _frontend_event(p, "start", "ps2", "/roms/ps2/GT4.iso", "GT4")
    s = p.autotune.session
    slot = p.run(p.power_slot(esde))
    print("second session ceiling", s.ceiling_tdp, "pick shown", slot["pick"])
    assert s.ceiling_tdp == 30, f"the ceiling moved to {s.ceiling_tdp} W because Autotune saved learning"


def test_r3_the_toast_says_nothing_was_saved_when_it_was(plugin):
    """An emulator that draws the whole limit at every limit, holding a reachable 30 fps target under the limit: the
    governor never needs the full limit, so its reference (`max_fps`) stays None. The session settles and
    _save_learned writes `learned`; the toast (summary) says "nothing new saved"."""
    p = plugin
    p.settings["autotune_target"] = 30
    p.run(p.set_tdp(30))
    p.run(p.game_started("77"))
    s = p.autotune.session
    _Game(p.autotune, lambda tdp, *_: 30.0 if tdp >= 14 else 2.1 * tdp).run(150)
    print("ref", s.gov.ref, "settled", s.settled, "tdp", s.learned.tdp, "held", dict(s.gov.held_s))
    r = p.run(p.game_stopped("77"))
    own = p.profiles.own("77", False)
    print("saved:", own.get("learned", {}).get("start"), "toast:", r["learned"]["summary"])
    assert not ("learned" in own and "nothing new saved" in r["learned"]["summary"]), "toast contradicts the save"


from test_backend import _tuner  # noqa: E402
from clawdeck import thermal  # noqa: E402


def test_r3_a_guard_step_whose_write_fails_leaves_no_cap_the_guard_cannot_lift(fake_hw, monkeypatch, tmp_path):
    """The guard steps (95 °C); the TDP write fails once. thermal_step returns False, so the guard records no step,
    but Autotune keeps thermal_cap. The package cools; the guard, with nothing recorded, never calls thermal_release:
    the governor works under the cap for the rest of the session."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp)).run(30)          # needs 20 W
    held = s.learned.tdp
    temp = tmp_path / "temp"
    temp.write_text("97000\n")
    g = thermal.Guard(str(temp), "pkg")
    ok = {"n": 0}
    real = at.power.set_tdp

    def flaky(w, plugged=None):
        ok["n"] += 1
        return False if ok["n"] == 1 else real(w, plugged)
    monkeypatch.setattr(at.power, "set_tdp", flaky)
    g.check(at.held_w(), 8, at.thermal_step, limit_w=30, raise_tdp=at.thermal_release)
    print("after the failed step: guard stepped_w", g.stepped_w, "autotune thermal_cap", s.thermal_cap, "held", held)
    temp.write_text("60000\n")                                       # cooled down, for minutes
    for _ in range(20):
        g.check(at.held_w(), 8, at.thermal_step, limit_w=30, raise_tdp=at.thermal_release)
    print("after 20 cool checks: thermal_cap", s.thermal_cap)
    assert s.thermal_cap is None, "a cap nobody will lift"


@pytest.mark.parametrize("bad", [{"start": "14"}, {"start": None, "tdp": "x"}, {"start": [14]}, {"start": 14.7}, {"start": -3}, {"start": 1e9}, {"start": True}])
def test_r3_a_hand_edited_or_foreign_record_does_not_stop_the_game_from_starting(plugin, bad):
    """profiles.json is a user file ("shareable as JSON"). A learned record with a start that is not a number must not
    keep the game's power and Autotune from starting."""
    p = plugin
    p.run(p.set_tdp(30))
    p.profiles.set("55", False, {"learned": {"ceiling": 30, **bad}})
    try:
        r = p.run(p.game_started("55"))
    except Exception as e:                       # noqa: BLE001
        pytest.fail(f"{bad}: game_started raised {type(e).__name__}: {e}; session={p.autotune.session is not None}, "
                    f"governed={p.power.governed}, loop={'gov None' if p.autotune.session and p.autotune.session.gov is None else 'ok'}")
    s = p.autotune.session
    print(bad, "->", s.learned.tdp, p.chip())
    assert 8 <= s.learned.tdp <= 30 and isinstance(s.learned.tdp, int)


def test_r3_xemu_already_running_gets_no_write_of_autotunes_own(plugin, tmp_path, monkeypatch):
    """R6: xemu, no write. A session that starts while xemu already runs (resume, a new power source, the frontend
    restarting a session) writes Autotune's start level and the 8 s window, and only the own-pace check 3 s later puts
    the pick back: 3 s of xemu at mid-range after every resume."""
    p = plugin
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "comm").write_text("xemu\n")
    monkeypatch.setattr(autotune, "PROC", str(proc))
    p.run(p.set_tdp(30))
    p.run(p.game_started("3119184808"))
    print("PL1 right after the start, xemu running:", p.chip(), "W; governed", p.power.governed)
    assert p.chip() == 30, "Autotune's start level is on the chip of a game it must stay out of"


def test_r3_a_pause_of_half_a_minute_does_not_throw_away_what_the_session_held(plugin, monkeypatch):
    """A game holds 14 W for two minutes; the player pauses it (no frames, e.g. a paused game that stops presenting)
    for 31 s; frames come back for 40 s; the game is closed. The session held 14 W for 2 minutes: the saved start
    should say so. The give-up replaces the governor (its held_s) and restarts at the full limit."""
    p = plugin
    p.settings["autotune_target"] = 30
    p.run(p.set_tdp(30))
    p.run(p.game_started("88"))
    s = p.autotune.session
    fps = {"on": True}
    g = _Game(p.autotune, lambda tdp, *_: (30.0 if tdp >= 14 else 2.1 * tdp) if fps["on"] else None)
    import time as _t
    monkeypatch.setattr(autotune, "time", type("C", (), {"monotonic": staticmethod(lambda: g.t), "time": staticmethod(_t.time)}))
    g.run(150)
    held_before = dict(s.gov.held_s)
    fps["on"] = False
    g.run(31)
    print("after the pause: paused", s.paused, "no_frames", s.no_frames, "chip", p.chip())
    fps["on"] = True
    g.run(40)
    print("held before the pause", held_before, "governor held now", dict(s.gov.held_s), "chip", p.chip())
    r = p.run(p.game_stopped("88"))
    own = p.profiles.own("88", False).get("learned", {})
    print("saved start", own.get("start"), "saved tdp", own.get("tdp"), "held", own.get("held"))
    assert own.get("start") == 14, f"saved start {own.get('start')} (tdp {own.get('tdp')})"
