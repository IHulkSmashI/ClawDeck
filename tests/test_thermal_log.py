"""thermal.csv: one row per guard check with the package temperature, its power, the TDP and the fans."""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from clawdeck import thermal  # noqa: E402


def _rows(p):
    with open(p, encoding="utf-8") as f:
        return list(csv.reader(f))


def test_rows_have_a_header_power_and_fans(tmp_path, monkeypatch):
    energy = tmp_path / "energy_uj"
    energy.write_text("1000000\n")
    clock = iter([100.0, 110.0])
    monkeypatch.setattr(thermal.time, "monotonic", lambda: next(clock))
    r = thermal.Recorder(str(tmp_path / "thermal.csv"), str(energy))
    fans = [{"fan": 1, "rpm": 4100, "enable": 2}, {"fan": 2, "rpm": 3900, "enable": 2}]
    r.row(96.4, 35, None, fans)
    energy.write_text("351000000\n")                      # 350 J in 10 s
    r.row(91.0, 33, 33, fans)
    rows = _rows(tmp_path / "thermal.csv")
    assert rows[0] == thermal.CSV_FIELDS
    assert rows[1][1:] == ["96", "", "35", "", "msi", "4100", "3900"]   # first row: no power yet
    assert rows[2][1:] == ["91", "35.0", "33", "33", "msi", "4100", "3900"]


def test_no_fans_no_energy_and_no_path(tmp_path):
    r = thermal.Recorder(str(tmp_path / "thermal.csv"))
    r.row(None, None, None, [])
    assert _rows(tmp_path / "thermal.csv")[1][1:] == ["", "", "", "", "", "", ""]
    thermal.Recorder(None).row(90.0, 30, None, [])     # no log directory: nothing written, no error


def test_a_full_file_moves_to_dot_1(tmp_path, monkeypatch):
    monkeypatch.setattr(thermal, "CSV_MAX_BYTES", 10)
    p = tmp_path / "thermal.csv"
    r = thermal.Recorder(str(p))
    r.row(50.0, 30, None, [])
    r.row(51.0, 30, None, [])
    assert os.path.exists(str(p) + ".1") and len(_rows(p)) == 2       # header + the new row
