# FlightLog Black Box: Project Guide

The whole project in one place: what it does, how it is built, and how to run it on your own PC,
including Google sign-in and n8n. Read it top to bottom the first time; after that, use the
headings as a checklist.

---

## 1. What it does

After every test flight someone used to open the ArduPilot log in Mission Planner and judge the
graphs by eye. FlightLog Black Box does that automatically:

> Drop a flight log into a Google Drive folder. About a minute later the team gets an email with
> a **Healthy / Warning / Critical** verdict, the reasons and the report files.

```
Google Drive folder ──► n8n ──► FastAPI (api.py) ──► blackbox.analyze() ──► verdict + reports
   (new log file)       │                                                        │
                        └──────────────── Gmail email with attachments ◄─────────┘
```

- **n8n** watches the Drive folder, downloads each new file, sends it to the API and emails the result.
- **api.py** (FastAPI) receives the file, runs the analysis and returns JSON plus report files.
- **blackbox/** (Python) does the actual work: validation, parsing, 13 health checks and the verdict.

---

## 2. What is in the repository

```
flightlog-blackbox/
├── blackbox/              the analysis core (Python package)
│   ├── validate.py        file type detection (.bin / .tlog / .rlog), SHA-256, duplicates
│   ├── parse.py           .bin (DataFlash) → tables
│   ├── tlog.py            .tlog (telemetry) → the same tables, reduced detail
│   ├── windows.py         armed / airborne / hover / altitude-hold time windows
│   ├── checks.py          the 13 health checks
│   ├── verdict.py         Healthy / Warning / Critical
│   ├── pipeline.py        analyze(log_path): the one entry point
│   ├── report.py          summary text and email
│   └── __main__.py        command line: python -m blackbox LOG
├── api.py                 FastAPI: POST /analyze, GET /files/...
├── thresholds.yaml        every limit the checks use (tune here, not in code)
├── n8n/
│   ├── flightlog_workflow.json   import this into n8n
│   └── validate_workflow.js      checks the workflow against n8n's node definitions
├── tests/                 pytest: 46 tests on synthetic logs, plus real-log tests
│   ├── dflog.py           writes synthetic .bin logs
│   ├── tlogsim.py         writes synthetic .tlog / .rlog files
│   └── data/              put real logs here (not in git)
├── output/                analysis results land here (not in git)
├── requirements.txt
└── README.md              short technical reference
```

The flight logs themselves are **not** in the repository (they are large and personal). Get them
from your partner separately and put them anywhere, for example `tests/data/`.

---

## 3. The checks

Each check looks only at the right part of the flight. Time on the bench before arming and the
takeoff and landing moments are ignored. Any Critical makes the flight Critical, any Warning makes
it Warning; "Not available" (the log lacks that data) does not change the verdict.

| # | Check | Measured from | Warning | Critical |
|---|---|---|---|---|
| 1 | Vibration | VIBE X/Y/Z, sustained max | > 30 m/s² | > 60 m/s² |
| 2 | Accelerometer clipping | VIBE.Clip rise while airborne | any | > 100 |
| 3 | Cell voltage under load | BAT.Volt ÷ cells, sustained min | < 3.5 V | < 3.3 V |
| 4 | Voltage sag | VoltR − Volt per cell | > 0.3 V | > 0.5 V |
| 5 | Board supply | POWR.Vcc while armed | < 4.8 V | < 4.5 V |
| 6 | Hover throttle | median CTUN.ThO in hover | > 0.60 | > 0.75 |
| 7 | Altitude hold | \|DAlt − Alt\| 95th percentile, altitude modes | > 0.3 m | > 0.6 m |
| 8 | Attitude tracking | roll/pitch error 95th percentile | > 5° | > 10° |
| 9 | Estimator health | XKF4 SV/SP/SH held > 1 s | > 0.8 | > 1.0 |
| 10 | Motor balance | spread of motor outputs, worst 5 s of hover | > 100 µs | > 200 µs |
| 11 | Hard landing | impact g and descent rate at touchdown | > 3 g / 1 m/s | > 5 g / 2 m/s |
| 12 | Errors and failsafes | ERR records while armed | any | crash, EKF, thrust loss |
| 13 | External navigation gaps | VISP/VISV/VISO gaps | > 0.2 s | > 0.5 s |

All limits live in `thresholds.yaml`. Vibration and EKF limits come from the ArduPilot docs; the
rest are team-set starting values to tune on clean flights.

**File types** (detected from the content, not the extension):

| File | Result |
|---|---|
| `.bin` from the SD card | Full analysis |
| `.tlog` from Mission Planner | Analysed with reduced detail (2–4 Hz, no impact data) |
| `.rlog` | Rejected: no timestamps; use the matching `.tlog` |
| Anything else | Rejected as "not an ArduPilot log" |

**Exceptions handled:** E1 not a log · E2 truncated or corrupt (analysed, marked Incomplete) ·
E3 missing messages (check shows Not available) · E4 duplicate upload · E5 never armed ·
E7 API offline (n8n retries, then emails). E6 (logbook open in Excel) belongs to the Excel
logbook, which is not built yet.

---

## 4. Install (once per PC)

You need:

- **Git**: https://git-scm.com/download/win
- **Python 3.11** or newer: https://www.python.org/downloads/ (tick "Add python.exe to PATH")
- **Node.js 24** or newer: https://nodejs.org (needed for n8n)

Get the code (replace the URL with the one your partner sends):

```bash
git clone https://github.com/<owner>/flightlog-blackbox.git
```

```bash
cd flightlog-blackbox
```

Later, to get your partner's newest changes:

```bash
git pull
```

Create the Python environment and install the packages (from inside `flightlog-blackbox`):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

If PowerShell refuses to run scripts later, run this once in that terminal:

```bash
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

---

## 5. Check that it works

Run the tests (about 25 seconds; "46 passed, 5 skipped" is correct, the skips need real logs):

```bash
.venv\Scripts\python -m pytest -q
```

Analyse a log from the command line:

```bash
.venv\Scripts\python -X utf8 -m blackbox "C:\path\to\2026-09-28 17-17-40.bin"
```

You get the verdict and one line per check. Add `--json` for the full result.

---

## 6. Start the API

Open a terminal in `flightlog-blackbox` and run:

```bash
.venv\Scripts\python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Leave it running. Check it in the browser: http://localhost:8000/health should show `{"ok":true}`.
http://localhost:8000/docs shows every endpoint and lets you upload a file by hand.

---

## 7. Start n8n and import the workflow

n8n runs on your own PC through `npx`; nothing is installed globally.

**7.1 Import the workflow** (once, in a second terminal inside `flightlog-blackbox`):

```bash
npx n8n@2.43.7 import:workflow --input=n8n/flightlog_workflow.json
```

The first run downloads n8n and takes a few minutes. It ends with "Successfully imported 1 workflow."

**7.2 Start n8n:**

```bash
npx n8n@2.43.7
```

Wait for `Editor is now accessible via: http://localhost:5678`. Leave this terminal running too:
**two terminals stay open, the API and n8n.**

**7.3 Open http://localhost:5678.** The first time, n8n asks you to set up an owner account:
email, name and a password of your choice. It only exists on your PC; it is not your Gmail
password. Skip the survey and the license offer if they appear.

**7.4 Open the workflow.** On the Overview page click **FlightLog Black Box**. You see a yellow
note and six boxes (nodes) joined by arrows:

| Node | Job |
|---|---|
| Drive trigger | Checks the Drive folder every minute for new files |
| Download log | Downloads the new file |
| Analyze log | Sends it to the API |
| Attach reports | Turns the API's results into email attachments |
| Email debrief | Emails the verdict to the team |
| Email: service offline | Warns the team if the API did not answer |

Double-clicking a node opens its settings. If the workflow list is empty, create a new workflow,
click **⋯ → Import from File…** and pick `n8n/flightlog_workflow.json`.

---

## 8. Google Cloud: let n8n use your Drive and Gmail (once)

This creates a "client ID", the key n8n uses to ask Google for permission. It is free. Ignore any
"free trial is over / Upgrade" banner: these APIs do not need billing.

**8.1 Project.** Go to https://console.cloud.google.com. Click the project button at the top left
(next to "Google Cloud") → **New project** → name `FlightLog` → **Create** → **Select project**.

**8.2 APIs.** In the top search bar type `Google Drive API` → open it → **Enable**.
Then search `Gmail API` → **Enable**.

**8.3 Consent screen.** Search `OAuth consent screen` (may be called *Google Auth Platform*) →
**Get started**:
- App name `FlightLog`, support email: your Gmail → **Next**
- Audience: **External** → **Next**
- Contact email: your Gmail → **Next** → tick the agreement → **Create**

Then in the left menu **Audience** → **Test users** → **Add users** → your Gmail → **Save**.
Every Google account that will sign in from n8n must be on this list.

**8.4 Client ID.** Left menu **Clients** → **Create client**:
- Application type: **Web application**, name `n8n`
- **Authorized redirect URIs** → **Add URI** → `http://localhost:5678/rest/oauth2-credential/callback`
- **Create**

Copy the **Client ID** and **Client secret** now (or **Download JSON**): the secret is shown only
once. **Never put them in the repository or in a chat.**

---

## 9. Connect Google inside n8n

**9.1 A test log in Drive.** In Google Drive create a folder `flight-logs` and upload one log into
it. Open the folder: the address ends in `/folders/1AbC...xyz`. The part after `/folders/` is the
**folder ID**.

**9.2 Drive credential.** In n8n double-click **Drive trigger** →
**Credential to connect with** → **Create new credential** → paste Client ID and Client secret →
**Sign in with Google** → pick your Gmail. On "Google hasn't verified this app" click
**Advanced → Go to FlightLog (unsafe)** (normal for your own test app) → tick every permission →
**Continue**. n8n shows "Account connected". Close the credential window.

**9.3 Folder.** Still in Drive trigger, at **Folder** replace `REPLACE_WITH_FOLDER_ID` with your
folder ID, or switch **By ID** to **From list** and pick `flight-logs`. Go back to the canvas.

**9.4** Double-click **Download log** → choose the same credential.

**9.5 Gmail credential.** Double-click **Email debrief** → **Create new credential** (a Gmail
OAuth2 API credential) → same Client ID and secret → **Sign in with Google** → same steps as 9.2.
In **To**, replace `REPLACE_WITH_TEAM_EMAIL` with the team's addresses (comma-separated).

**9.6** Double-click **Email: service offline** → same Gmail credential and **To**.

**9.7 API address.** Double-click **Analyze log** → change the URL to
`http://localhost:8000/analyze?inline=true`
(the file says `host.docker.internal`, which is only right when n8n runs in Docker).

**9.8** Click **Save** (Ctrl+S).

---

## 10. Test and switch on

**10.1 Test run.** Click **Execute workflow** at the bottom of the canvas. Each node turns green
in turn. An email arrives, titled for example *"[FlightLog] Critical: 2026-09-28 17-17-40.bin"*,
with `summary.txt` and `result.json` attached. Click a node to see the data it produced.

**10.2 Offline test (E7).** Stop the API (Ctrl+C in its terminal) and execute again: Analyze log
retries 3 times, 5 s apart, then *Email: service offline* is sent. Start the API again afterwards.

**10.3 Switch on.** Flip the **Active** toggle at the top right (called **Publish** in some 2.x
versions). From now on every new file in `flight-logs` is analysed and emailed within about a
minute. Only files added after this point trigger it.

**Before recording the demo:** Google sign-ins of apps in *Testing* mode expire after **7 days**.
Open each credential in n8n and click **Sign in with Google** again shortly before recording.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `npx n8n` shows nothing for minutes | First run is downloading. If it ends with `ECONNRESET`, run it again. |
| Drive trigger: "No data" | Wrong folder ID, or the folder is empty. |
| Analyze log: connection refused | The API terminal is not running (section 6), or the URL still says `host.docker.internal`. |
| Google: "Access blocked … has not completed verification" | Your Gmail is not in **Test users** (8.3). |
| Google: "redirect_uri_mismatch" | The redirect URI in 8.4 does not exactly match `http://localhost:5678/rest/oauth2-credential/callback`. |
| Gmail: insufficient permissions | A permission box was not ticked; reconnect the credential. |
| Email arrives but says "Already processed" | That exact file was analysed before (E4). Delete `output/processed.json` to reset during testing. |
| PowerShell: "running scripts is disabled" | Run the `Set-ExecutionPolicy` line from section 4. |

After editing the workflow in n8n you can check it against n8n's own node definitions:

```bash
node n8n/validate_workflow.js %LOCALAPPDATA%\npm-cache\_npx\<hash>\node_modules n8n/flightlog_workflow.json
```

(`<hash>` is the folder under `npm-cache\_npx` that contains `node_modules\n8n`.)

---

## 12. What the API returns (for the Excel logbook and PowerPoint debrief)

`POST /analyze` returns JSON like this (shortened):

```json
{
  "flight_id": "2026-09-28_17-17-40_c0190b2e",
  "status": "Analysed",
  "verdict": "Critical",
  "incomplete": false,
  "exception_id": null,
  "reason": "",
  "source": "dataflash",
  "flight_date": null,
  "email": {"subject": "[FlightLog] Critical: ...", "body": "..."},
  "files": [{"name": "summary.txt", "url": "/files/<flight_id>/summary.txt"}, ...],
  "result": {
    "file_name": "...", "sha256": "...", "armed_s": 142.0, "airborne_s": 132.0, "firmware": "...",
    "checks": [{"id": 3, "name": "Cell voltage under load", "status": "Critical", "value": 2.876,
                "warning": 3.5, "critical": 3.3, "unit": "V", "t_flight_s": 31.0, "detail": "..."}, ...],
    "findings": [3, 4, 7, 8, 10],
    "notes": []
  }
}
```

`status` is one of `Analysed`, `Rejected`, `Duplicate`, `No flight`. In Python the same data
comes from `blackbox.analyze(path)` (an `AnalysisResult`; `.to_dict()` gives the JSON above).

**Where the outputs plug in:**

- Write the deck and the logbook in `api.py` → `_write_reports()` and add their file names to
  `REPORT_FILES`; n8n then attaches them automatically.
- The **Flights** sheet maps to `result` (one row per flight, one column per check status).
  **Findings** maps to the checks whose status is Warning or Critical. **Exceptions** maps to
  results whose `status` is not `Analysed`, plus `reason`.
- Once the Flights sheet stores SHA-256 hashes, pass them to `analyze(..., known_hashes=...)` in
  place of `output/processed.json`.
- openpyxl drops existing charts when it re-saves a workbook, so rebuild the Trends charts on
  every save.

---

## 13. Status

**Done**
- Core: validation (E1–E5), `.bin` and `.tlog` parsing, flight windows, 13 checks, verdict.
- API with download endpoint, duplicate registry and request lock.
- n8n workflow, imported and validated against n8n 2.43.7's node definitions.
- 46 tests on synthetic logs; tested on the team's real logs (2 `.bin`, 17 `.tlog`, 15 `.rlog`).

**Still to do**
- Excel logbook (Flights, Findings, Exceptions, Trends) and E6 (logbook open in Excel).
- PowerPoint debrief (8 slides) and the plots.
- Streamlit upload page (`app.py`): call `blackbox.analyze()` or `POST /analyze`.
- Tune `thresholds.yaml` on 2–3 clean flights of the airframe in the report and record the values.
- Report, workflow diagrams, screenshots, demo video.

**Findings on the real logs so far** (same BOTWINGF405 GPS quad in every file):
- Heavy voltage sag on every flight; on 24-09 and 28-09 17:31 the voltage collapsed, ArduPilot
  logged "Potential Thrust Loss" and the drone came down. Check the pack, connector and wiring.
- The 15-10 flights use a 3S pack while `MOT_BAT_VOLT_MAX` is still 25.2 V (6S).
- The quad is so overpowered it climbs at minimum throttle, which shows up as altitude-hold errors.
