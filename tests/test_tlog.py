"""Telemetry (.tlog) support and .rlog rejection."""

import pytest

from blackbox import analyze
from blackbox.checks import Status
from blackbox.pipeline import ANALYSED, REJECTED
from blackbox.validate import DATAFLASH, RAW_LINK, TELEMETRY, log_kind
import tlogsim


def check(res, cid):
    return next(c for c in res.checks if c.id == cid)


@pytest.fixture
def make_tlog(tmp_path):
    def _make(name="flight.tlog", **kw):
        tlog, rlog = tlogsim.flight(**kw)
        path = tmp_path / name
        path.write_bytes(tlog)
        raw = tmp_path / (path.stem + ".rlog")
        raw.write_bytes(rlog)
        return path, raw
    return _make


def test_detects_kind_by_content(make_tlog, make_log, tmp_path):
    tlog, rlog = make_tlog()
    assert log_kind(tlog) == TELEMETRY
    assert log_kind(rlog) == RAW_LINK
    assert log_kind(make_log()) == DATAFLASH
    renamed = tmp_path / "actually_a_tlog.bin"   # extension must not decide the reader
    renamed.write_bytes(tlog.read_bytes())
    assert analyze(renamed).source == TELEMETRY


def test_clean_tlog_is_healthy_with_reduced_detail_note(make_tlog, cfg):
    res = analyze(make_tlog()[0], cfg)
    assert res.status == ANALYSED and res.source == TELEMETRY
    assert res.verdict == "Healthy", [(c.id, c.status.value, c.value, c.detail) for c in res.checks
                                       if c.status != Status.PASS]
    assert res.flight_date.startswith("2026-09-21")
    assert res.firmware.startswith("ArduCopter V4.7.0")
    assert any("reduced detail" in n for n in res.notes)
    assert "6S from MOT_BAT_VOLT_MAX" in check(res, 3).detail
    assert check(res, 5).status == Status.NOT_AVAILABLE       # POWER_STATUS.Vcc = 0
    assert check(res, 11).unit == "m/s"                       # no IMU: descent rate only
    assert check(res, 13).status == Status.NOT_AVAILABLE
    for cid in (1, 2, 6, 7, 8, 9, 10):
        assert check(res, cid).status == Status.PASS, check(res, cid)


def test_tlog_low_battery(make_tlog, cfg):
    res = analyze(make_tlog(rest_end_cell=3.35, res_ohm=0.25)[0], cfg)
    assert check(res, 3).status == Status.CRITICAL
    sag = check(res, 4)
    assert sag.status == Status.CRITICAL and "first 10 s airborne" in sag.detail


def test_statustext_errors(make_tlog, cfg):
    res = analyze(make_tlog(statustexts=[(40.0, "Potential Thrust Loss (3)"), (50.0, "GPS Glitch or Compass error")])[0],
                  cfg)
    c = check(res, 12)
    assert c.status == Status.CRITICAL and "THRUST_LOSS_CHECK" in c.detail and "GPS" in c.detail
    # The PreArm radio-failsafe text before arming is ignored.
    assert "FAILSAFE_RADIO" not in c.detail


def test_tlog_cut_while_armed_is_incomplete(make_tlog, cfg):
    res = analyze(make_tlog(end=60.0)[0], cfg)
    assert res.status == ANALYSED and res.incomplete and res.exception_id == "E2"


def test_rlog_rejected_with_clear_message(make_tlog, cfg):
    res = analyze(make_tlog()[1], cfg)
    assert res.status == REJECTED and res.exception_id == "E1"
    assert ".rlog" in res.reason and ".tlog" in res.reason


def test_rlog_with_boot_noise_is_still_recognised(make_tlog, tmp_path):
    # Seen on the team's 2026-09-28 17-24-53.rlog: boot text before the first MAVLink frame.
    noisy = tmp_path / "noisy.rlog"
    noisy.write_bytes(b"Init Gyro**" + make_tlog()[1].read_bytes())
    assert log_kind(noisy) == RAW_LINK


def test_random_bytes_are_not_mavlink(tmp_path):
    import numpy as np
    junk = tmp_path / "thumb.jpeg"
    junk.write_bytes(b"\xff\xd8\xff\xe0" + np.random.default_rng(0).integers(0, 256, 4092, dtype=np.uint8).tobytes())
    assert log_kind(junk) is None


def test_stale_cell_param_loses_to_voltage(make_tlog, cfg, tmp_path):
    # MOT_BAT_VOLT_MAX says 6S (25.2 V) but a 3S pack is fitted: trust the measured voltage.
    from blackbox.config import cells_for
    assert cells_for(cfg, 0, {"MOT_BAT_VOLT_MAX": 25.2}, 12.3) == (3, "voltage at arming (set cells in thresholds.yaml to be sure)")
    assert cells_for(cfg, 0, {"MOT_BAT_VOLT_MAX": 25.2}, 24.9)[0] == 6


def test_back_to_back_takeoffs_do_not_crash_sag(make_tlog, cfg):
    # Seen on the 2026-10-15 tlogs: two takeoffs inside one 10 s sag window.
    from blackbox import tlog
    from blackbox.checks import Ctx, voltage_sag
    from blackbox.windows import segment
    parsed = tlog.load(make_tlog()[0])
    win = segment(parsed, cfg)
    s, e = win.airborne[0]
    win.airborne = [(s, s + 4.0), (s + 6.0, e)]
    assert voltage_sag(Ctx(parsed.tables, parsed.params, win, cfg)).status != Status.NOT_AVAILABLE
