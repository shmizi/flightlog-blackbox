"""FastAPI service and the n8n workflow file."""

import base64
import importlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dflog

WORKFLOW = Path(__file__).resolve().parent.parent / "n8n" / "flightlog_workflow.json"
WORKFLOW_GDRIVE = WORKFLOW.with_name("flightlog_workflow_gdrive.json")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTLOG_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("FLIGHTLOG_LOGBOOK_RETRY_S", "0")
    import api
    importlib.reload(api)        # pick up the temp output folder
    return TestClient(api.app), api


def upload(c, name, data, inline=False):
    r = c.post("/analyze" + ("?inline=true" if inline else ""), files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()


def test_analyze_then_download(client):
    c, api = client
    body = upload(c, "00000042.BIN", dflog.flight())
    assert body["status"] == "Analysed" and body["verdict"] == "Healthy"
    assert body["email"]["subject"] == "[FlightLog] Healthy: 00000042.BIN"
    assert "Findings: none" in body["email"]["body"]
    assert "attachments" not in body
    names = {f["name"] for f in body["files"]}
    assert names == {"summary.txt", "result.json"}
    r = c.get(f"/files/{body['flight_id']}/result.json")
    assert r.status_code == 200 and r.json()["verdict"] == "Healthy"
    assert (api.OUTPUT / body["flight_id"] / "00000042.BIN").exists()      # log archived with its reports


def test_inline_attachments_for_email(client):
    c, _ = client
    body = upload(c, "crash.bin", dflog.flight(weak_motor_at=50.0, gps=True,
                                               params={"BATT_LOW_VOLT": 10.5}), inline=True)
    assert body["verdict"] == "Critical"
    att = {a["fileName"]: a for a in body["attachments"]}
    # result.json is for software: stored and downloadable, not emailed.
    assert set(att) == {"summary.txt", "flight_path.kml", "suggested_fixes.param", "flight_logbook.xlsx"}
    assert "result.json" in {f["name"] for f in body["files"]}
    assert att["flight_path.kml"]["mimeType"] == "application/vnd.google-earth.kml+xml"
    assert "flight_path.kml (open in Google Earth" in body["email"]["body"]
    text = base64.b64decode(att["summary.txt"]["data"]).decode("utf-8")
    assert "[CRITICAL] 10. Motor balance" in text and att["summary.txt"]["mimeType"] == "text/plain"


def test_repeat_upload_gets_full_report_with_a_note(client):
    c, api = client
    data = dflog.flight(weak_motor_at=50.0, params={"BATT_LOW_VOLT": 10.5})
    first = upload(c, "a.bin", data)
    again = upload(c, "copy of a.bin", data, inline=True)
    assert again["status"] == "Analysed" and again["verdict"] == "Critical"
    assert again["flight_id"] == first["flight_id"] and "as a.bin" in again["repeat_of"]
    assert again["email"]["subject"] == "[FlightLog] Critical: copy of a.bin"
    assert "This exact file was already analysed" in again["email"]["body"]
    assert {a["fileName"] for a in again["attachments"]} == {"summary.txt", "suggested_fixes.param", "flight_logbook.xlsx"}
    # Counted once: one ledger entry, one history line, one copy of the log.
    assert c.get("/ledger").json()["entries"] == 1
    assert len(api.HISTORY.read_text(encoding="utf-8").splitlines()) == 1
    assert not (api.OUTPUT / first["flight_id"] / "copy of a.bin").exists()


def test_flight_processed_before_the_logbook_existed_is_entered_once(client):
    c, api = client
    data = dflog.flight()
    upload(c, "old.bin", data)
    api.LOGBOOK_RECORDS.unlink()          # as on a setup that processed logs before the logbook existed
    api.HISTORY.unlink()
    again = upload(c, "old.bin", data)
    assert again["repeat_of"]             # the ledger still knows it
    recs = [json.loads(line) for line in api.LOGBOOK_RECORDS.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in recs] == [None]                       # entered as a flight, not a repeat
    assert len(api.HISTORY.read_text(encoding="utf-8").splitlines()) == 1
    upload(c, "old.bin", data)
    recs = [json.loads(line) for line in api.LOGBOOK_RECORDS.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in recs] == [None, "repeat"]
    assert len(api.HISTORY.read_text(encoding="utf-8").splitlines()) == 1


def test_simultaneous_repeats_count_once(client):
    c, api = client
    data = dflog.flight()
    with ThreadPoolExecutor(4) as pool:
        bodies = list(pool.map(lambda i: upload(c, f"same_{i}.bin", data), range(4)))
    assert all(b["status"] == "Analysed" for b in bodies)
    assert sum(b["repeat_of"] is None for b in bodies) == 1
    assert c.get("/ledger").json()["entries"] == 1
    assert len(api.HISTORY.read_text(encoding="utf-8").splitlines()) == 1


def test_rejected_file_is_not_stored(client):
    c, api = client
    body = upload(c, "photo.jpeg", b"\xff\xd8\xff\xe0" + b"\0" * 500, inline=True)
    assert body["status"] == "Rejected" and body["exception_id"] == "E1" and body["files"] == []
    assert body["email"]["subject"] == "[FlightLog] Rejected: photo.jpeg"
    assert [p for p in api.OUTPUT.iterdir() if p.is_dir()] == []


def test_download_rejects_path_tricks(client):
    c, api = client
    upload(c, "x.bin", dflog.flight())
    assert c.get("/files/..%2F..%2Fapi.py/x").status_code == 404
    assert c.get("/files/processed.json/x").status_code == 404
    assert c.get("/files/nope/summary.txt").status_code == 404


def test_second_flight_is_compared_with_the_first(client):
    c, _ = client
    first = upload(c, "a.bin", dflog.flight())
    assert first["result"]["history"][0].startswith("First flight on record")
    second = upload(c, "b.bin", dflog.flight(vibe=40, seed=5))
    hist = second["result"]["history"]
    assert hist[0].startswith("Compared with the last 1 flight") and any(h.startswith("Vibration:") for h in hist)
    assert "Earlier flights" in second["email"]["html"]


def test_ledger_detects_tampering(client):
    c, api = client
    for i in range(3):
        upload(c, f"f{i}.bin", dflog.flight(seed=10 + i))
    ok = c.get("/ledger").json()
    assert ok["intact"] and ok["entries"] == 3
    reg = json.loads(api.REGISTRY.read_text(encoding="utf-8"))
    middle = list(reg)[1]
    reg[middle]["verdict"] = "Healthy" if reg[middle]["verdict"] != "Healthy" else "Critical"   # quietly edit history
    api.REGISTRY.write_text(json.dumps(reg), encoding="utf-8")
    bad = c.get("/ledger").json()
    assert not bad["intact"] and bad["broken_at"] == "f1.bin"


def test_preflight_param_file_through_api(client):
    c, api = client
    text = "MOT_BAT_VOLT_MAX,25.2\nBATT_LOW_VOLT,10.6\nBATT_FS_LOW_ACT,0\nBATT_MONITOR,4\n"
    body = upload(c, "quad.param", text.encode(), inline=True)
    assert body["status"] == "Pre-flight check" and body["verdict"] == "NO-GO"
    assert body["email"]["subject"] == "[FlightLog] Pre-flight NO-GO: quad.param"
    assert {a["fileName"] for a in body["attachments"]} == {"summary.txt", "suggested_fixes.param", "flight_logbook.xlsx"}
    assert [p for p in api.OUTPUT.iterdir() if p.is_dir()] == []      # checked again every time, not stored


# n8n workflow -------------------------------------------------------------------
@pytest.mark.parametrize("path", [WORKFLOW, WORKFLOW_GDRIVE], ids=["folder+smtp", "gdrive+gmail"])
def test_workflow_is_wired_and_has_the_fixes(path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in wf["nodes"]}
    for src, outputs in wf["connections"].items():
        assert src in nodes
        for branch in outputs["main"]:
            for link in branch:
                assert link["node"] in nodes, link
    http = nodes["Analyze log"]
    assert http["retryOnFail"] and http["maxTries"] == 3 and http["onError"] == "continueErrorOutput"   # E7
    assert http["parameters"]["options"]["batching"]["batch"]["batchSize"] == 1     # one log at a time
    assert "inline=true" in http["parameters"]["url"]
    assert wf["connections"]["Analyze log"]["main"][1][0]["node"] == "Email: service offline"
    assert len({n["id"] for n in wf["nodes"]}) == len(wf["nodes"])


def test_setup_fills_every_placeholder():
    """scripts/setup_n8n.py must leave no __PLACEHOLDER__ behind and point both emails at its credential."""
    import sys
    sys.path.insert(0, str(WORKFLOW.parent.parent / "scripts"))
    import setup_n8n
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    filled = setup_n8n._replace(wf, {"__WATCH_FOLDER__": r"C:\logs", "__API_URL__": "http://127.0.0.1:8000",
                                     "__FROM_EMAIL__": "a@gmail.com", "__TO_EMAIL__": "b@x.com"})
    text = json.dumps(filled)
    assert "__" not in text.replace("__esModule", "")
    nodes = {n["name"]: n for n in filled["nodes"]}
    assert nodes["Watch log folder"]["parameters"]["path"] == r"C:\logs"
    assert nodes["Analyze log"]["parameters"]["url"].startswith("http://127.0.0.1:8000/")   # not localhost (IPv6 ::1)
    for name in ("Email debrief", "Email: service offline"):
        assert nodes[name]["credentials"]["smtp"]["id"] == setup_n8n.CREDENTIAL_ID


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_workflow_code_node_runs(client):
    """Run the Code node's JavaScript on a real API response, the way n8n would."""
    c, _ = client
    body = upload(c, "f.bin", dflog.flight(params={"BATT_LOW_VOLT": 10.5}), inline=True)
    code = next(n for n in json.loads(WORKFLOW.read_text(encoding="utf-8"))["nodes"]
                if n["name"] == "Attach reports")["parameters"]["jsCode"]
    harness = f"const $json = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
              f"const out = (() => {{ {code} }})();\nconsole.log(JSON.stringify(out));"
    run = subprocess.run(["node", "-e", harness], input=json.dumps(body), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["json"]["attachmentNames"] == ",".join(f"attachment_{i}" for i in range(len(body["attachments"])))
    assert "attachments" not in out["json"] and out["json"]["email"]["subject"].endswith("f.bin")
    assert out["binary"]["attachment_0"]["fileName"] == "summary.txt"
