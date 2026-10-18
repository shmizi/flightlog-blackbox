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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTLOG_OUTPUT_DIR", str(tmp_path / "output"))
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
    body = upload(c, "crash.bin", dflog.flight(weak_motor_at=50.0), inline=True)
    assert body["verdict"] == "Critical"
    att = {a["fileName"]: a for a in body["attachments"]}
    assert set(att) == {"summary.txt", "result.json"}
    text = base64.b64decode(att["summary.txt"]["data"]).decode("utf-8")
    assert "[CRITICAL] 10. Motor balance" in text and att["summary.txt"]["mimeType"] == "text/plain"


def test_duplicate_points_at_original(client):
    c, _ = client
    data = dflog.flight()
    first = upload(c, "a.bin", data)
    again = upload(c, "copy of a.bin", data, inline=True)
    assert again["status"] == "Duplicate" and again["exception_id"] == "E4"
    assert again["flight_id"] == first["flight_id"] and "as a.bin" in again["reason"]
    assert [a["fileName"] for a in again["attachments"]] == ["summary.txt"]
    assert again["email"]["subject"].startswith("[FlightLog] Already processed")


def test_simultaneous_duplicates_count_once(client):
    c, _ = client
    data = dflog.flight()
    with ThreadPoolExecutor(4) as pool:
        bodies = list(pool.map(lambda i: upload(c, f"same_{i}.bin", data), range(4)))
    assert sorted(b["status"] for b in bodies) == ["Analysed", "Duplicate", "Duplicate", "Duplicate"]


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


# n8n workflow -------------------------------------------------------------------
def test_workflow_is_wired_and_has_the_fixes():
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_workflow_code_node_runs(client):
    """Run the Code node's JavaScript on a real API response, the way n8n would."""
    c, _ = client
    body = upload(c, "f.bin", dflog.flight(), inline=True)
    code = next(n for n in json.loads(WORKFLOW.read_text(encoding="utf-8"))["nodes"]
                if n["name"] == "Attach reports")["parameters"]["jsCode"]
    harness = f"const $json = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
              f"const out = (() => {{ {code} }})();\nconsole.log(JSON.stringify(out));"
    run = subprocess.run(["node", "-e", harness], input=json.dumps(body), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["json"]["attachmentNames"] == "attachment_0,attachment_1"
    assert "attachments" not in out["json"] and out["json"]["email"]["subject"].endswith("f.bin")
    assert out["binary"]["attachment_0"]["fileName"] == "summary.txt"
