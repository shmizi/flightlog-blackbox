"""Targeted tests for windowing, instance handling, E3 and individual checks."""

import shutil

from blackbox import analyze, parse
from blackbox.checks import Status
from blackbox.windows import segment


def check(res, cid):
    return next(c for c in res.checks if c.id == cid)


def test_windows_from_events(make_log, cfg):
    win = segment(parse.load(make_log()), cfg)
    assert len(win.armed) == 1 and abs(win.armed[0][0] - 5.0) < 0.01 and abs(win.armed[0][1] - 103.0) < 0.01
    assert len(win.airborne) == 1
    takeoff, touchdown = win.airborne[0]
    assert abs(takeoff - 7.0) < 0.01
    assert abs(touchdown - 100.2) < 0.01          # LAND_COMPLETE_MAYBE, not LAND_COMPLETE + 1 s
    assert win.touchdowns == [touchdown]
    assert win.hover and win.alt_ctrl
    assert not win.ends_armed and not win.airborne_inferred


def test_airborne_inferred_from_altitude_without_events(make_log, cfg):
    path = make_log(drop={"EV"})                   # ARM still marks arming; no NOT_LANDED
    win = segment(parse.load(path), cfg)
    assert win.airborne_inferred
    assert len(win.airborne) == 1 and win.airborne_s > 80


def test_file_without_bin_extension_parses(make_log, cfg, tmp_path):
    # mavutil.mavlink_connection would treat this as a telemetry log; we open DFReader directly.
    src = make_log()
    tmp = tmp_path / "upload_7f3a.tmp"
    shutil.copy(src, tmp)
    assert analyze(tmp, cfg).verdict == "Healthy"


def test_e3_missing_messages_are_not_available(make_log, cfg):
    res = analyze(make_log(drop={"VIBE", "RCOU"}), cfg)
    assert res.verdict == "Healthy"
    for cid in (1, 2, 10):
        c = check(res, cid)
        assert c.status == Status.NOT_AVAILABLE
        assert "LOG_BITMASK" in c.detail


def test_hover_throttle_uses_output_not_capped_estimate(make_log, cfg):
    # ArduPilot caps the learned ThH at 0.6875, so a 0.75 critical limit on ThH could never fire.
    res = analyze(make_log(hover_thr=0.80), cfg)
    c = check(res, 6)
    assert c.status == Status.CRITICAL and abs(c.value - 0.80) < 0.02
    assert check(analyze(make_log(hover_thr=0.65), cfg), 6).status == Status.WARNING


def test_resolved_errors_are_ignored(make_log, cfg):
    res = analyze(make_log(errors=[(30.0, 6, 1), (31.0, 6, 0)]), cfg)
    c = check(res, 12)
    assert c.status == Status.WARNING and c.value == 1 and "FAILSAFE_BATT/1" in c.detail


def test_ekf_failsafe_error_is_critical(make_log, cfg):
    res = analyze(make_log(errors=[(40.0, 17, 1)]), cfg)
    assert check(res, 12).status == Status.CRITICAL


def test_estimator_needs_ratio_held(make_log, cfg):
    short = analyze(make_log(ekf_sp=(40.0, 40.5, 1.3)), cfg)
    assert check(short, 9).status == Status.PASS
    held = analyze(make_log(ekf_sp=(40.0, 42.0, 1.3)), cfg)
    c = check(held, 9)
    assert c.status == Status.CRITICAL and "SP on core 0" in c.detail


def test_attitude_and_altitude(make_log, cfg):
    res = analyze(make_log(att_err=16.0), cfg)
    assert check(res, 8).status in (Status.WARNING, Status.CRITICAL)
    assert check(res, 7).status == Status.PASS


def test_altitude_hold_not_available_in_stabilize(make_log, cfg):
    res = analyze(make_log(mode=0), cfg)
    assert check(res, 7).status == Status.NOT_AVAILABLE


def test_hard_landing(make_log, cfg):
    res = analyze(make_log(hard_landing=True), cfg)
    c = check(res, 11)
    assert c.status == Status.CRITICAL and "descent" in c.detail


def test_external_nav_gap(make_log, cfg):
    ok = analyze(make_log(ext_nav=True), cfg)
    assert check(ok, 13).status == Status.PASS
    gap = analyze(make_log(ext_nav=True, ext_nav_gap=(50.0, 0.8)), cfg)
    assert check(gap, 13).status == Status.CRITICAL
    assert check(analyze(make_log(), cfg), 13).status == Status.NOT_AVAILABLE


# Regressions found on the team's real logs (2026-09-24, 2026-09-28) ------------
def test_aborted_arm_does_not_switch_to_altitude_inference(make_log, cfg):
    win = segment(parse.load(make_log(aborted_arm=True)), cfg)
    assert len(win.armed) == 2 and len(win.airborne) == 1
    assert not win.airborne_inferred


def test_board_without_vcc_sensing(make_log, cfg):
    c = check(analyze(make_log(vcc_nan=True), cfg), 5)
    assert c.status == Status.NOT_AVAILABLE and "does not measure board voltage" in c.detail


def test_cells_auto_from_mot_bat_volt_max(make_log, cfg):
    res = analyze(make_log(cells=6, params={"MOT_BAT_VOLT_MAX": 25.2}), cfg)
    c = check(res, 3)
    assert c.status == Status.PASS and "6S from MOT_BAT_VOLT_MAX" in c.detail


def test_cells_auto_from_voltage_and_pinned(make_log, cfg):
    path = make_log(cells=6)
    assert "6S from voltage at arming" in check(analyze(path, cfg), 3).detail
    cfg["battery"]["cells"] = 4                    # pinned wrong on purpose: flagged, not hidden
    c = check(analyze(path, cfg), 3)
    assert "is cells=4 right?" in c.detail


def test_errors_after_disarm_are_ignored(make_log, cfg):
    res = analyze(make_log(errors=[(103.8, 11, 2)]), cfg)   # GPS glitch 0.8 s after disarm
    assert check(res, 12).status == Status.PASS


def test_result_serialises(make_log, cfg):
    import json
    d = analyze(make_log(vibe=45), cfg).to_dict()
    json.dumps(d)
    assert d["verdict"] == "Warning" and 1 in d["findings"]
