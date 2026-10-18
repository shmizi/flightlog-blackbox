"""FastAPI service that n8n and Streamlit call.

    POST /analyze                  multipart field "file"; ?inline=true also returns the
                                   report files base64-encoded, ready to attach to an email
    GET  /files/{flight_id}/{name} download one report file
    GET  /health

Run:  .venv\\Scripts\\python -m uvicorn api:app --host 0.0.0.0 --port 8000
"""

import base64
import json
import mimetypes
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from filelock import FileLock, Timeout

from blackbox import analyze, report
from blackbox.pipeline import ANALYSED, DUPLICATE, NO_FLIGHT
from blackbox.validate import sha256

OUTPUT = Path(os.environ.get("FLIGHTLOG_OUTPUT_DIR", Path(__file__).resolve().parent / "output")).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
REGISTRY = OUTPUT / "processed.json"      # SHA-256 -> first processing, for E4 until the Excel logbook exists
LOCK = FileLock(str(OUTPUT / ".lock"), timeout=120)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")
# Report files written per flight. The partner's debrief deck goes here too when it exists.
REPORT_FILES = ("summary.txt", "result.json")

app = FastAPI(title="FlightLog Black Box", version="1.0")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/analyze")
def analyze_upload(file: UploadFile = File(...), inline: bool = Query(False)):
    # A plain def runs in FastAPI's thread pool, so a long analysis does not block other requests.
    name = Path(file.filename or "upload.bin").name
    with tempfile.TemporaryDirectory(dir=OUTPUT) as tmp:
        path = Path(tmp) / name
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f, 1 << 20)
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File larger than {MAX_UPLOAD_BYTES >> 20} MB")
        digest = sha256(path)
        flight_id = _flight_id(name, digest)

        # One request at a time reads and writes the registry, so two copies of the same log
        # uploaded together cannot both be counted (E4), and no registry update is lost.
        try:
            with LOCK:
                registry = _load_registry()
                known = {h: f"{e['processed']} as {e['file_name']}" for h, e in registry.items()}
                res = analyze(path, known_hashes=known)
                files = []
                if res.status in (ANALYSED, NO_FLIGHT):
                    out_dir = OUTPUT / flight_id
                    out_dir.mkdir(exist_ok=True)
                    shutil.move(str(path), out_dir / name)
                    files = _write_reports(res, out_dir)
                    registry[digest] = {"processed": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                        "file_name": name, "flight_id": flight_id,
                                        "status": res.status, "verdict": res.verdict}
                    _save_registry(registry)
                elif res.status == DUPLICATE:
                    flight_id = registry[digest]["flight_id"]
                    files = _list_files(OUTPUT / flight_id)
        except Timeout:
            raise HTTPException(503, "Analysis busy; try again")

    mail = report.email(res)
    body = {
        "flight_id": flight_id,
        "status": res.status,
        "verdict": res.verdict,
        "incomplete": res.incomplete,
        "exception_id": res.exception_id,
        "reason": res.reason,
        "source": res.source,
        "flight_date": res.flight_date,
        "email": mail,
        "files": [{"name": p.name, "url": f"/files/{flight_id}/{p.name}"} for p in files],
        "result": res.to_dict(),
    }
    if inline:
        # Stored reports, or just the summary for a rejected or duplicate file.
        attach = [(p.name, p.read_bytes()) for p in files if p.name in REPORT_FILES] if files else []
        if not attach or res.status == DUPLICATE:
            attach = [("summary.txt", mail["body"].encode("utf-8"))]
        body["attachments"] = [{"fileName": n, "mimeType": _mime(n), "data": base64.b64encode(b).decode("ascii")}
                               for n, b in attach]
    return body


@app.get("/files/{flight_id}/{name}")
def download(flight_id: str, name: str):
    if not (SAFE_NAME.fullmatch(flight_id) and SAFE_NAME.fullmatch(name)):
        raise HTTPException(404, "Not found")
    path = (OUTPUT / flight_id / name).resolve()
    if path.parent.parent != OUTPUT or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, filename=name, media_type=_mime(name))


def _write_reports(res, out_dir):
    (out_dir / "summary.txt").write_text(report.summary_text(res), encoding="utf-8")
    (out_dir / "result.json").write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return [out_dir / n for n in REPORT_FILES]


def _list_files(folder):
    return [folder / n for n in REPORT_FILES if (folder / n).is_file()]


def _flight_id(name, digest):
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:40] or "log"
    return f"{stem}_{digest[:8]}"


def _load_registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {}


def _save_registry(registry):
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    os.replace(tmp, REGISTRY)     # atomic: a crash mid-write never leaves half a registry


def _mime(name):
    return mimetypes.guess_type(name)[0] or ("text/plain" if name.endswith(".txt") else "application/octet-stream")
