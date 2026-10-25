"""Recommended actions, configuration check and the email texts."""

from blackbox import analyze, report
from blackbox.checks import Status


def check(res, cid):
    return next(c for c in res.checks if c.id == cid)


def test_hard_landing_shows_the_measurement_that_set_the_verdict(make_log, cfg):
    # 4 g impact is only a warning, but 2.5 m/s descent is critical: show the descent, not the g.
    c = check(analyze(make_log(hard_landing=True, impact_g=4.0), cfg), 11)
    assert c.status == Status.CRITICAL and c.unit == "m/s" and c.critical == 2.0 and c.value > 2.0
    c = check(analyze(make_log(hard_landing=True, impact_g=6.0), cfg), 11)
    assert c.unit == "g" and c.value > 5.0


def test_configuration_check(make_log, cfg):
    assert check(analyze(make_log(), cfg), 14).status == Status.PASS
    res = analyze(make_log(params={"MOT_BAT_VOLT_MAX": 25.2, "BATT_LOW_VOLT": 10.5, "BATT_FS_LOW_ACT": 0}), cfg)
    c = check(res, 14)
    assert c.status == Status.WARNING and c.value == 3
    assert "set for a 6S pack but a 4S" in c.detail and "per cell" in c.detail and "BATT_FS_LOW_ACT" in c.detail


def test_errors_use_plain_names(make_log, cfg):
    c = check(analyze(make_log(weak_motor_at=50.0), cfg), 12)
    assert "Potential thrust loss" in c.detail and "Crash detected" in c.detail
    assert "(THRUST_LOSS_CHECK/1)" in c.detail


def test_battery_collapse_is_linked_to_thrust_loss(make_log, cfg):
    res = analyze(make_log(rest_end_cell=3.40, res_ohm=0.10, errors=[(80.0, 25, 1)]), cfg)
    assert res.actions[0].startswith("Ground this battery")
    assert not any(a.startswith("The pack dropped") for a in res.actions)     # covered by the linked cause


def test_motor_action_names_the_output(make_log, cfg):
    res = analyze(make_log(weak_motor_at=50.0), cfg)
    assert any("motor on output 1" in a for a in res.actions)
    assert any(a.startswith("A crash was detected") for a in res.actions)


def test_healthy_flight_needs_no_action(make_log, cfg):
    assert analyze(make_log(), cfg).actions == ["No action needed. Keep this flight as a reference for the next ones."]


def test_email_text_and_html(make_log, cfg):
    res = analyze(make_log(weak_motor_at=50.0, vcc_nan=True), cfg)
    mail = report.email(res)
    assert mail["subject"] == "[FlightLog] Critical: flight.bin"
    assert "Recommended actions:" in mail["body"]
    html = mail["html"]
    assert "#d93025" in html and "Recommended actions" in html and "<table" in html
    # Checks that do not apply to this drone are left out of both versions.
    for text in (mail["body"], html):
        assert "Board supply" not in text.split("Passed:")[0]
        assert "External navigation" not in text


def test_html_is_escaped(make_log, cfg):
    res = analyze(make_log(), cfg)
    res.file_name = "<script>x</script>.bin"
    assert "<script>" not in report.summary_html(res)
