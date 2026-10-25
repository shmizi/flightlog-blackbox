"""FastAPI service that n8n and Streamlit call.

    POST /analyze                  multipart field "file" (.bin, .tlog or a .param for a pre-flight
                                   check); ?inline=true also returns the email attachments base64-encoded
    GET  /files/{flight_id}/{name} download one report file
    GET  /logbook                  download the Excel flight logbook
    GET  /ledger                   verify the tamper-evident chain of processed logs
    GET  /health

Run:  .venv\\Scripts\\python -m uvicorn api:app --host 0.0.0.0 --port 8000
"""

import base64
import hashlib
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

from blackbox import analyze, exports, history, logbook, report
from blackbox.pipeline import ANALYSED, NO_FLIGHT
from blackbox.validate import sha256

OUTPUT = Path(os.environ.get("FLIGHTLOG_OUTPUT_DIR", Path(__file__).resolve().parent / "output")).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
REGISTRY = OUTPUT / "processed.json"      # SHA-256 -> first processing (E4 repeat note) and the tamper-evident chain
HISTORY = OUTPUT / "history.jsonl"        # one line of metrics per analysed flight, for comparisons
LOGBOOK_RECORDS = OUTPUT / "logbook.jsonl"   # one line per processed file; the workbook is built from it
LOGBOOK = OUTPUT / "flight_logbook.xlsx"
LOGBOOK_RETRY_S = float(os.environ.get("FLIGHTLOG_LOGBOOK_RETRY_S", "10"))   # E6: wait between save attempts
LOCK = FileLock(str(OUTPUT / ".lock"), timeout=120)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")
GENESIS = "0" * 64
# Files kept per flight. result.json is for software (logbook, debrief, Streamlit), so it is
# stored and downloadable but not emailed. The partner's debrief deck goes here too when it exists.
REPORT_FILES = ("summary.txt", "flight_path.kml", "suggested_fixes.param", "result.json")

app = FastAPI(title="FlightLog Black Box", version="1.1")


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

        # One request at a time reads and writes the registry and history, so two copies of the same
        # log uploaded together cannot both be counted (E4), and no update is lost.
        try:
            with LOCK:
                registry = _load_registry()
                known = {h: f"{e['processed']} as {e['file_name']}" for h, e in registry.items()}
                res = analyze(path, known_hashes=known)
                extras = exports.build(res)
                earlier = logbook.load(HISTORY)
                if res.status == ANALYSED:
                    res.history = history.compare(res.metrics, earlier)
                processed = datetime.now().strftime("%Y-%m-%d %H:%M")
                records = logbook.load(LOGBOOK_RECORDS)
                # A repeat only counts as a repeat if the logbook already has the flight: files processed
                # before the logbook existed are entered properly the first time they come back.
                in_book = any(r["result"].get("sha256") == res.sha256 and r.get("event") != "repeat" for r in records)
                rec = logbook.record(res, processed, "repeat" if res.repeat_of and in_book else None)
                logbook.append(LOGBOOK_RECORDS, rec)
                book_path, pending = logbook.save(records + [rec], LOGBOOK, wait_s=LOGBOOK_RETRY_S)
                if pending:
                    res.notes.append("The Excel logbook was open in Excel, so this update was saved as "
                                     "flight_logbook_pending.xlsx; it is merged automatically next time (E6).")
                extras["flight_logbook.xlsx"] = book_path.read_bytes()
                mail = report.email(res, ["summary.txt", *extras])
                files = []
                if res.status in (ANALYSED, NO_FLIGHT):
                    if res.repeat_of:
                        # A repeat upload refreshes the first upload's reports; the log, ledger entry and
                        # history line already exist, so nothing is counted twice.
                        flight_id = registry[digest]["flight_id"]
                    out_dir = OUTPUT / flight_id
                    out_dir.mkdir(exist_ok=True)
                    if not res.repeat_of:
                        shutil.move(str(path), out_dir / name)
                    files = _write_reports(res, out_dir, mail["body"],
                                           {k: v for k, v in extras.items() if k in REPORT_FILES})
                    if not res.repeat_of:
                        _add_to_ledger(registry, digest, {
                            "processed": processed, "file_name": name,
                            "flight_id": flight_id, "status": res.status, "verdict": res.verdict})
                        _save_registry(registry)
                    known_flight = any(h.get("sha256") == res.sha256 for h in earlier)
                    if res.status == ANALYSED and not known_flight:
                        logbook.append(HISTORY, res.metrics)
        except Timeout:
            raise HTTPException(503, "Analysis busy; try again")

    body = {
        "flight_id": flight_id,
        "status": res.status,
        "verdict": res.verdict,
        "incomplete": res.incomplete,
        "exception_id": res.exception_id,
        "reason": res.reason,
        "repeat_of": res.repeat_of,
        "source": res.source,
        "flight_date": res.flight_date,
        "email": mail,
        "files": [{"name": p.name, "url": f"/files/{flight_id}/{p.name}"} for p in files],
        "result": res.to_dict(),
    }
    if inline:
        attach = [("summary.txt", mail["body"].encode("utf-8")), *extras.items()]
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


@app.get("/logbook")
def download_logbook():
    if not LOGBOOK.is_file():
        raise HTTPException(404, "No flights in the logbook yet")
    return FileResponse(LOGBOOK, filename=LOGBOOK.name, media_type=_mime(LOGBOOK.name))


@app.get("/ledger")
def ledger():
    """Recompute the hash chain: any edited, removed or reordered entry breaks it from that point on."""
    with LOCK:
        registry = _load_registry()
    prev = GENESIS
    for digest, entry in registry.items():
        if entry.get("chain") != _chain(prev, digest, entry):
            return {"entries": len(registry), "intact": False, "broken_at": entry.get("file_name")}
        prev = entry["chain"]
    return {"entries": len(registry), "intact": True, "broken_at": None, "head": prev}


def _chain(prev, digest, entry):
    record = "|".join([prev, digest, entry["processed"], entry["file_name"], entry["status"], str(entry["verdict"])])
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def _add_to_ledger(registry, digest, entry):
    """Append an entry whose hash covers the previous entry's hash (a hash chain)."""
    prev = GENESIS
    for d, e in registry.items():        # entries from before the ledger existed get chained once
        if "chain" not in e:
            e["chain"] = _chain(prev, d, e)
        prev = e["chain"]
    entry["chain"] = _chain(prev, digest, entry)
    registry[digest] = entry


def _write_reports(res, out_dir, summary, extras):
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    (out_dir / "result.json").write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    for fname, data in extras.items():
        (out_dir / fname).write_bytes(data)
    return _list_files(out_dir)


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
    if name.endswith(".kml"):
        return "application/vnd.google-earth.kml+xml"
    if name.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name.endswith((".txt", ".param")):
        return "text/plain"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
