"""Excel logbook (incl. E6) and the Streamlit dashboard."""

import importlib
import io
import os
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

import dflog
from blackbox import analyze, logbook

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def records(make_log, cfg, tmp_path):
    good = analyze(make_log("a.bin"), cfg)
    crash = analyze(make_log("b.bin", weak_motor_at=50.0, params={"BATT_LOW_VOLT": 10.5}), cfg)
    junk = tmp_path / "photo.bin"
    junk.write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 100)
    rejected = analyze(junk, cfg)
    repeat = analyze(make_log("a.bin"), cfg, known_hashes={good.sha256: "2026-10-21 10:00 as a.bin"})
    param = tmp_path / "quad.param"
    param.write_text("MOT_BAT_VOLT_MAX,16.8\nBATT_LOW_VOLT,10\nBATT_FS_LOW_ACT,0\n")
    pre = analyze(param, cfg)
    return [logbook.record(good, "2026-10-21 10:00"), logbook.record(crash, "2026-10-21 10:05"),
            logbook.record(rejected, "2026-10-21 10:06"), logbook.record(repeat, "2026-10-21 10:07", "repeat"),
            logbook.record(pre, "2026-10-21 10:08")]


def test_workbook_sheets(records):
    wb = logbook.build(records)
    assert wb.sheetnames == ["Flights", "Findings", "Exceptions", "Pre-flight", "Trends"]
    flights = list(wb["Flights"].iter_rows(min_row=2, values_only=True))
    assert [r[2] for r in flights] == ["a.bin", "b.bin"]          # the repeat is not a second flight
    assert flights[1][5] == "Critical" and wb["Flights"]["F3"].fill.fgColor.rgb.endswith("FFC7CE")
    findings = list(wb["Findings"].iter_rows(min_row=2, values_only=True))
    assert any(r[3] == "10. Motor balance" and r[4] == "Critical" for r in findings)
    exceptions = list(wb["Exceptions"].iter_rows(min_row=2, values_only=True))
    assert {r[2] for r in exceptions} == {"E1", "E4"}
    pre = list(wb["Pre-flight"].iter_rows(min_row=2, values_only=True))
    assert pre[0][2] == "NO-GO"
    assert len(wb["Trends"]._charts) == 4


def test_e6_logbook_open_in_excel(records, tmp_path, monkeypatch):
    path = tmp_path / "flight_logbook.xlsx"
    real_save = openpyxl.Workbook.save

    def locked(self, filename):
        if Path(filename) == path:
            raise PermissionError("open in Excel")
        return real_save(self, filename)

    monkeypatch.setattr(openpyxl.Workbook, "save", locked)
    written, pending = logbook.save(records, path, retries=3, wait_s=0)
    assert pending and written.name == "flight_logbook_pending.xlsx" and written.exists()
    monkeypatch.setattr(openpyxl.Workbook, "save", real_save)      # Excel closed again
    written, pending = logbook.save(records, path, retries=3, wait_s=0)
    assert not pending and path.exists() and not (tmp_path / "flight_logbook_pending.xlsx").exists()


def test_api_keeps_logbook_and_serves_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTLOG_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("FLIGHTLOG_LOGBOOK_RETRY_S", "0")
    import api
    importlib.reload(api)
    c = TestClient(api.app)
    assert c.get("/logbook").status_code == 404
    for name, data in (("a.bin", dflog.flight()), ("b.bin", dflog.flight(vibe=40, seed=4)), ("a again.bin", dflog.flight())):
        c.post("/analyze", files={"file": (name, data)})
    r = c.get("/logbook")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb["Flights"].max_row == 3                       # header + 2 flights; the repeat is an exception
    assert any(row[2] == "E4" for row in wb["Exceptions"].iter_rows(min_row=2, values_only=True))


# Dashboard -------------------------------------------------------------------
@pytest.fixture
def fake_n8n(monkeypatch):
    """Something answering /healthz like a running n8n."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/healthz" else 404)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("FLIGHTLOG_N8N_URL", f"http://127.0.0.1:{server.server_port}")
    yield
    server.shutdown()


def _app(tmp_path, monkeypatch, records=None, folder_files=None):
    from streamlit.testing.v1 import AppTest
    out = tmp_path / "dash"
    out.mkdir()
    watch = tmp_path / "watch"
    watch.mkdir()
    for name, data in (folder_files or {}).items():
        (watch / name).write_bytes(data)
    for rec in records or []:
        logbook.append(out / "logbook.jsonl", rec)
    monkeypatch.setenv("FLIGHTLOG_OUTPUT_DIR", str(out))
    monkeypatch.setenv("FLIGHTLOG_WATCH_FOLDER", str(watch))
    monkeypatch.setenv("FLIGHTLOG_API_URL", "http://127.0.0.1:9")    # nothing listens there: API off
    if "FLIGHTLOG_N8N_URL" not in os.environ:
        monkeypatch.setenv("FLIGHTLOG_N8N_URL", "http://127.0.0.1:9")    # n8n off unless fake_n8n runs
    monkeypatch.chdir(ROOT)
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()


def test_dashboard_with_empty_logbook(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch)
    assert not at.exception
    assert any("No files processed yet" in i.value for i in at.info)


def test_dashboard_shows_a_file_from_the_watched_folder(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch, folder_files={"crash.bin": dflog.flight(weak_motor_at=50.0, gps=True)})
    at.selectbox[0].set_value("crash.bin").run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Critical" in html and "Recommended actions" in html and "Flight path" in html
    assert {b.label for b in at.get("download_button")} >= {"summary.txt", "flight_path.kml"}


def test_dashboard_upload_goes_to_the_watched_folder_when_n8n_runs(tmp_path, monkeypatch, fake_n8n):
    data = dflog.flight(weak_motor_at=50.0)
    at = _app(tmp_path, monkeypatch, folder_files={"crash.bin": b"an older file with the same name"})
    at.get("file_uploader")[0].upload("crash.bin", data).run()
    assert not at.exception
    watch = tmp_path / "watch"
    # n8n only reacts to new files, so the existing crash.bin is kept and the upload gets a new name.
    assert (watch / "crash (2).bin").read_bytes() == data
    assert (watch / "crash.bin").read_bytes() == b"an older file with the same name"
    assert not list(watch.glob("*.part"))
    assert any("Sent to the automation as watch\\crash (2).bin" in c.value for c in at.caption)
    assert "Critical" in " ".join(m.value for m in at.markdown)
    at.tabs[1].run()                                   # any rerun must not drop the file a second time
    assert sorted(p.name for p in watch.iterdir()) == ["crash (2).bin", "crash.bin"]


def test_dashboard_upload_without_n8n_or_api_is_only_analysed(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch)
    at.get("file_uploader")[0].upload("a.bin", dflog.flight()).run()
    assert not at.exception
    assert list((tmp_path / "watch").iterdir()) == []
    assert any("not logged or emailed" in c.value for c in at.caption)


def test_dashboard_logbook_tab(tmp_path, monkeypatch, records):
    at = _app(tmp_path, monkeypatch, records)
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Flights"] == "2" and metrics["Critical"] == "1" and metrics["Ledger"] == "API off"
