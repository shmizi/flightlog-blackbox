"""Test plan T1-T8.

Each scenario runs on a synthetic DataFlash log so the suite passes before
any real flight. Drop real logs into tests/data/ under the names in
REAL_LOGS and the matching real-log test runs too.
"""

from pathlib import Path

import pytest

from blackbox import analyze
from blackbox.checks import Status
from blackbox.pipeline import ANALYSED, DUPLICATE, NO_FLIGHT, REJECTED

DATA = Path(__file__).parent / "data"


def status_of(res, check_id):
    return next(c.status for c in res.checks if c.id == check_id)


def bad(res):
    return {c.id for c in res.checks if c.status in (Status.WARNING, Status.CRITICAL)}


def test_t1_clean_flight_is_healthy(make_log, cfg):
    res = analyze(make_log(), cfg)
    assert res.status == ANALYSED and not res.incomplete
    assert res.verdict == "Healthy", [c for c in res.checks if c.status != Status.PASS]
    # Bench quirks before arming (0 V battery on USB, 4.6 V Vcc, radio failsafe) must not count.
    assert status_of(res, 3) == Status.PASS
    assert status_of(res, 5) == Status.PASS
    assert status_of(res, 12) == Status.PASS
    # IMU 1's clip counter sits at 37 all flight: per-instance evaluation sees no new clips.
    assert status_of(res, 2) == Status.PASS
    assert res.firmware.startswith("ArduCopter V4.5.7")


def test_t2_high_vibration(make_log, cfg):
    res = analyze(make_log(vibe=45, clip_rate=0.5), cfg)
    assert res.verdict in ("Warning", "Critical")
    assert status_of(res, 1) == Status.WARNING
    assert status_of(res, 2) in (Status.WARNING, Status.CRITICAL)


def test_t2_severe_vibration_is_critical(make_log, cfg):
    res = analyze(make_log(vibe=75, clip_rate=3), cfg)
    assert status_of(res, 1) == Status.CRITICAL
    assert status_of(res, 2) == Status.CRITICAL


def test_t3_low_battery_and_sag(make_log, cfg):
    res = analyze(make_log(rest_end_cell=3.40, res_ohm=0.10), cfg)
    assert res.verdict == "Critical"
    assert status_of(res, 3) == Status.CRITICAL
    assert status_of(res, 4) == Status.CRITICAL


def test_t4_motor_failure_and_crash(make_log, cfg):
    res = analyze(make_log(weak_motor_at=50.0), cfg)
    assert res.verdict == "Critical"
    for cid in (10, 11, 12):
        assert status_of(res, cid) == Status.CRITICAL, next(c for c in res.checks if c.id == cid)
    errors = next(c for c in res.checks if c.id == 12)
    assert "CRASH_CHECK" in errors.detail and "THRUST_LOSS_CHECK" in errors.detail


def test_t5_truncated_log_is_incomplete(make_log, cfg, tmp_path):
    full = make_log().read_bytes()
    cut = tmp_path / "truncated.bin"
    cut.write_bytes(full[: len(full) // 2])
    res = analyze(cut, cfg)
    assert res.status == ANALYSED
    assert res.incomplete and res.exception_id == "E2"
    assert "ends while still armed" in res.reason
    assert status_of(res, 11) == Status.NOT_AVAILABLE      # no landing in the surviving half


def test_t5_truncated_too_short_is_rejected(make_log, cfg, tmp_path):
    full = make_log().read_bytes()
    cut = tmp_path / "stub.bin"
    cut.write_bytes(full[: len(full) // 6])
    res = analyze(cut, cfg)
    assert res.status == REJECTED and res.exception_id == "E2"


def test_t6_not_a_log(tmp_path, cfg):
    fake = tmp_path / "photo.bin"
    fake.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 2000)   # JPEG header
    res = analyze(fake, cfg)
    assert res.status == REJECTED and res.exception_id == "E1"


def test_t7_duplicate(make_log, cfg):
    path = make_log()
    first = analyze(path, cfg)
    again = analyze(path, cfg, known_hashes={first.sha256: "2026-10-18"})
    assert again.status == DUPLICATE and again.exception_id == "E4"
    assert "2026-10-18" in again.reason


def test_t8_bench_log_never_armed(make_log, cfg):
    res = analyze(make_log(bench=True), cfg)
    assert res.status == NO_FLIGHT and res.exception_id == "E5"
    assert res.verdict is None


# Real logs -------------------------------------------------------------------
REAL_LOGS = {
    "T1_clean.bin": lambda r: r.verdict == "Healthy",
    "T2_vibration.bin": lambda r: bad(r) & {1, 2},
    "T3_battery.bin": lambda r: {3, 4} & {c.id for c in r.checks if c.status == Status.CRITICAL},
    "T4_motor_fail.bin": lambda r: {10, 11, 12} & {c.id for c in r.checks if c.status == Status.CRITICAL},
}


LOG_FILES = sorted(p for p in DATA.iterdir() if p.suffix.lower() in (".bin", ".tlog", ".rlog")) if DATA.exists() else []


@pytest.mark.parametrize("path", LOG_FILES or [None], ids=lambda p: p.name if p else "none")
def test_every_log_in_data_runs_cleanly(path, cfg):
    """Smoke test: any log dropped into tests/data/ parses and no check crashes."""
    if path is None:
        pytest.skip("no logs in tests/data/")
    res = analyze(path, cfg)
    assert res.status in (ANALYSED, REJECTED, NO_FLIGHT), res.reason
    assert not [c for c in res.checks if c.detail.startswith("Check failed")]


@pytest.mark.parametrize("name", REAL_LOGS)
def test_real_log(name, cfg):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"put a real log at tests/data/{name}")
    res = analyze(path, cfg)
    assert res.status == ANALYSED, res.reason
    assert REAL_LOGS[name](res), [(c.id, c.name, c.status.value, c.value, c.detail) for c in res.checks]
