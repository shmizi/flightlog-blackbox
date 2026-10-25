"""Checks 15-16, exports, flight history and the pre-flight .param check."""

from datetime import datetime, timezone

from blackbox import analyze, exports, history, validate
from blackbox.checks import Status
from blackbox.pipeline import PREFLIGHT


def check(res, cid):
    return next(c for c in res.checks if c.id == cid)


# 15: vibration spectrum ------------------------------------------------------
def test_spectrum_finds_peak_and_suggests_notch_when_off(make_log, cfg):
    c = check(analyze(make_log(vib_peak_hz=150), cfg), 15)
    assert c.status == Status.WARNING and abs(c.value - 150) < 3
    assert c.fixes["INS_HNTCH_ENABLE"][0] == 1 and abs(c.fixes["INS_HNTCH_FREQ"][0] - 150) < 3


def test_spectrum_fixed_notch_that_misses_the_peak(make_log, cfg):
    p = {"INS_HNTCH_ENABLE": 1, "INS_HNTCH_MODE": 1, "INS_HNTCH_FREQ": 80, "INS_HNTCH_BW": 40,
         "INS_HNTCH_REF": 0, "INS_HNTCH_HMNCS": 1}
    c = check(analyze(make_log(vib_peak_hz=150, params=p), cfg), 15)
    assert c.status == Status.WARNING and "misses it" in c.detail and "INS_HNTCH_REF = 0" in c.detail


def test_spectrum_notch_covering_the_peak_passes(make_log, cfg):
    p = {"INS_HNTCH_ENABLE": 1, "INS_HNTCH_MODE": 0, "INS_HNTCH_FREQ": 150, "INS_HNTCH_BW": 60, "INS_HNTCH_HMNCS": 1}
    assert check(analyze(make_log(vib_peak_hz=150, params=p), cfg), 15).status == Status.PASS


def test_spectrum_hidden_without_batch_data(make_log, cfg):
    c = check(analyze(make_log(), cfg), 15)
    assert c.status == Status.NOT_AVAILABLE and not c.applies


# 16: compass interference ----------------------------------------------------
def test_compass_interference(make_log, cfg):
    low = check(analyze(make_log(mag_slope=0.5), cfg), 16)
    assert low.status == Status.PASS and low.value < 10
    high = check(analyze(make_log(mag_slope=8.7), cfg), 16)        # about 50% of the Earth's field at 25 A
    assert high.status == Status.WARNING and 40 < high.value < 60
    off = check(analyze(make_log(mag_slope=8.7, params={"COMPASS_USE": 0}), cfg), 16)
    assert off.status == Status.NOT_AVAILABLE and not off.applies


# GPS date, flight path and parameter fixes ------------------------------------
def test_bin_gets_real_date_and_flight_path(make_log, cfg):
    res = analyze(make_log(gps=True), cfg)
    # GPS week 2437, 100000 s into the week, minus 18 leap seconds, minus 0.2 s of log before the fix.
    expected = datetime.fromtimestamp(315964800 + 2437 * 604800 + 100000.2 - 18 - 0.2 - 0.05, timezone.utc)
    assert res.flight_date[:16] == expected.isoformat()[:16]
    kml = exports.flight_kml(res)
    assert kml and "<LineString>" in kml and "Takeoff" in kml and "73.74" in kml
    assert "flight_path.kml" in exports.build(res)


def test_kml_pins_findings(make_log, cfg):
    res = analyze(make_log(gps=True, weak_motor_at=50.0), cfg)
    kml = exports.flight_kml(res)
    assert "Critical: Motor balance" in kml and "#crit" in kml


def test_no_gps_no_map(make_log, cfg):
    assert "flight_path.kml" not in exports.build(analyze(make_log(), cfg))


def test_param_fix_file(make_log, cfg):
    res = analyze(make_log(params={"BATT_LOW_VOLT": 10.5, "BATT_FS_LOW_ACT": 0}), cfg)
    text = exports.param_fixes(res)
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert "BATT_LOW_VOLT,14" in lines and "BATT_CRT_VOLT,13.2" in lines and "BATT_FS_LOW_ACT,1" in lines
    assert all(len(ln.split(",")) == 2 for ln in lines)                  # Mission Planner NAME,VALUE


# Flight history ---------------------------------------------------------------
def test_history_compares_with_same_drone_only(make_log, cfg):
    first = analyze(make_log(), cfg).metrics
    other_drone = dict(first, airframe="OTHER …1", vibration=99.0)
    worse = analyze(make_log(vibe=40, seed=3), cfg).metrics
    lines = history.compare(worse, [other_drone, first])
    assert lines[0].startswith("Compared with the last 1 flight of this drone (TESTF405 …0000ABCD)")
    assert any(ln.startswith("Vibration:") and "worse" in ln for ln in lines)
    assert history.compare(first, [])[0].startswith("First flight on record")


def test_metrics_include_battery_resistance(make_log, cfg):
    m = analyze(make_log(res_ohm=0.05), cfg).metrics
    assert m["batt_res_mohm"] == 50.0 and m["airframe"] == "TESTF405 …0000ABCD"


# Pre-flight GO / NO-GO ---------------------------------------------------------
SAFE = "MOT_BAT_VOLT_MAX,25.2\nBATT_MONITOR,4\nBATT_LOW_VOLT,21.0\nBATT_CRT_VOLT,19.8\nBATT_FS_LOW_ACT,2\n" \
       "FS_THR_ENABLE,1\nARMING_CHECK,1\nLOG_BITMASK,180222\nINS_HNTCH_ENABLE,0\n"


def test_param_file_detected(tmp_path):
    p = tmp_path / "quad.param"
    p.write_text("# saved by Mission Planner\n" + SAFE)
    assert validate.log_kind(p) == validate.PARAMS
    junk = tmp_path / "notes.txt"
    junk.write_text("hello\nthis is not\na parameter file\n")
    assert validate.log_kind(junk) is None


def test_preflight_go(tmp_path, cfg):
    p = tmp_path / "quad.param"
    p.write_text(SAFE)
    res = analyze(p, cfg)
    assert res.status == PREFLIGHT and res.verdict == "GO"
    assert res.actions == ["Parameters look safe to fly."]


def test_preflight_no_go_for_the_24_09_settings(tmp_path, cfg):
    p = tmp_path / "quad.param"
    p.write_text(SAFE.replace("BATT_LOW_VOLT,21.0", "BATT_LOW_VOLT,10.6").replace("BATT_FS_LOW_ACT,2", "BATT_FS_LOW_ACT,0"))
    res = analyze(p, cfg)
    assert res.verdict == "NO-GO"
    c = check(res, 101)
    assert c.status == Status.CRITICAL and c.fixes["BATT_LOW_VOLT"][0] == 21.0
    assert "suggested_fixes.param" in exports.build(res)


def test_preflight_qgc_format_and_warnings(tmp_path, cfg):
    p = tmp_path / "quad.params"
    p.write_text("# QGC\n1 1 MOT_BAT_VOLT_MAX 16.8 9\n1 1 BATT_LOW_VOLT 14.0 9\n1 1 FS_THR_ENABLE 0 2\n"
                 "1 1 LOG_BITMASK 0 6\n1 1 BATT_FS_LOW_ACT 1 2\n")
    res = analyze(p, cfg)
    assert res.verdict == "GO with warnings"
    assert check(res, 103).status == Status.WARNING and check(res, 105).status == Status.WARNING
