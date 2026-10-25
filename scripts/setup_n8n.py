"""One-time n8n setup: watched folder + Gmail sending, no Google Cloud needed.

    .venv\\Scripts\\python scripts\\setup_n8n.py

Asks for your Gmail address, who should get the emails and a Gmail App Password
(Google Account -> Security -> 2-Step Verification -> App passwords). It then
  1. creates the watched folder (default: flight-logs\\ in this repo),
  2. stores the email credential in n8n (encrypted by n8n, never written to the repo),
  3. imports the workflow with your folder and addresses filled in,
  4. switches the workflow on.
Run it again any time to change the folder, addresses or password; add --keep-credential to
change only the folder or addresses without re-entering the password.
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "n8n" / "flightlog_workflow.json"
N8N = "n8n@2.43.7"
CREDENTIAL_ID = "flightlogSmtp001"
CREDENTIAL_NAME = "FlightLog email (SMTP)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch-folder", default=str(ROOT / "flight-logs"))
    # 127.0.0.1, not localhost: Node 24 resolves localhost to IPv6 ::1, where uvicorn is not listening.
    ap.add_argument("--api-url", default="http://127.0.0.1:8000")
    ap.add_argument("--from-email", help="your Gmail address (asked if missing)")
    ap.add_argument("--to-email", help="comma-separated recipients (asked if missing)")
    # For testing against a local mail server instead of Gmail.
    ap.add_argument("--smtp-host", default="smtp.gmail.com")
    ap.add_argument("--smtp-port", type=int, default=465)
    ap.add_argument("--smtp-no-ssl", action="store_true")
    ap.add_argument("--no-password", action="store_true", help="SMTP server without login (local testing)")
    ap.add_argument("--keep-credential", action="store_true",
                    help="keep the email password already stored in n8n; only update the workflow")
    args = ap.parse_args()

    npx = shutil.which("npx")
    if not npx:
        sys.exit("Node.js is not installed (npx not found). Install it from https://nodejs.org and run this again.")

    sender = args.from_email or input("Your Gmail address (emails are sent from it): ").strip()
    to = args.to_email or input(f"Send the analysis to (comma-separated) [{sender}]: ").strip() or sender
    password = "" if args.no_password or args.keep_credential else ask_app_password()

    watch = Path(args.watch_folder).resolve()
    watch.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        if not args.keep_credential:
            cred_file = Path(tmp) / "credential.json"
            data = {"host": args.smtp_host, "port": args.smtp_port, "secure": not args.smtp_no_ssl}
            if not args.no_password:
                data.update(user=sender, password=password)
            cred_file.write_text(json.dumps([{"id": CREDENTIAL_ID, "name": CREDENTIAL_NAME, "type": "smtp",
                                              "data": data}]), encoding="utf-8")
            run(npx, "import:credentials", f"--input={cred_file}")
            cred_file.unlink()      # the plain-text copy exists only for this one command

        wf = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        fill = {"__WATCH_FOLDER__": str(watch), "__API_URL__": args.api_url.rstrip("/"),
                "__FROM_EMAIL__": sender, "__TO_EMAIL__": to}
        wf = _replace(wf, fill)
        wf_file = Path(tmp) / "workflow.json"
        wf_file.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        run(npx, "import:workflow", f"--input={wf_file}")
        run(npx, "publish:workflow", f"--id={wf['id']}")

    print("\nDone. The workflow is on.")
    print(f"  Watched folder: {watch}")
    print(f"  Emails go to:   {to}")
    print("Next: run scripts\\start.ps1, then drop a .bin or .tlog into the folder.")


def ask_app_password():
    print("Gmail App Password: Google Account > Security > 2-Step Verification > App passwords.")
    print("Tip: in VS Code's terminal, paste with a RIGHT-CLICK (Ctrl+V may not work in a hidden prompt).")
    for attempt in range(3):
        if attempt < 2:
            password = getpass.getpass("Paste it here (input is hidden), then press Enter: ")
        else:
            print("Hidden paste is not working in this terminal; this time the text will be visible.")
            password = input("Paste it here, then press Enter: ")
        password = password.replace(" ", "").strip()
        if len(password) == 16:
            return password
        print(f"  That was {len(password)} characters; a Gmail App Password has 16 letters. Try again.")
    sys.exit("No valid App Password entered. Nothing was changed; run the script again.")


def _replace(obj, fill):
    if isinstance(obj, dict):
        return {k: _replace(v, fill) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace(v, fill) for v in obj]
    if isinstance(obj, str):
        for key, value in fill.items():
            obj = obj.replace(key, value)
    return obj


def run(npx, *cmd):
    print(f"> n8n {cmd[0]}")
    env = {**os.environ, "N8N_DIAGNOSTICS_ENABLED": "false",
           "NODES_EXCLUDE": '["n8n-nodes-base.executeCommand"]'}
    proc = subprocess.run([npx, "--yes", N8N, *cmd], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines()
             if ln.strip() and "migration" not in ln and not ln.startswith("npm warn")]
    if proc.returncode != 0:
        print("\n".join(lines[-15:]))
        sys.exit(f"n8n {cmd[0]} failed (exit {proc.returncode}).")
    print("  " + (lines[-1] if lines else "ok"))


if __name__ == "__main__":
    main()
