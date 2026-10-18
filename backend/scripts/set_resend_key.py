"""
backend/scripts/set_resend_key.py

Install an email API key (Resend) into ``backend/.env`` from a local file, *verified* by
sending a real test message — so a typo or a revoked key can never be written into the
configuration and silently break alerting.

Usage (from backend/):

    python scripts/set_resend_key.py --to you@example.com
    python scripts/set_resend_key.py --to you@example.com --file /tmp/k.txt
    python scripts/set_resend_key.py --to you@example.com --from "ShelfMonitor <a@b.com>"

The same flow is available in the app (Alerts -> "Email setup"), which additionally
applies the key without a restart. This script exists for headless/desktop setups.

The script prints only a masked confirmation — the key value itself is never written to
stdout, and the input file is deleted once it has been verified and saved.
"""

from __future__ import annotations

import argparse
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services import email_setup  # noqa: E402

DEFAULT_KEY_FILE = os.path.join(BACKEND_DIR, ".resend_api_key.txt")


def _fail(message: str) -> "None":
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _read_key(path: str) -> str:
    if not os.path.isfile(path):
        _fail(f"key file not found: {path}")

    # Tolerate a stray "RESEND_API_KEY=..." prefix or surrounding quotes/whitespace.
    with open(path, "r", encoding="utf-8-sig") as fh:
        raw = fh.read()

    candidate = ""
    for line in raw.splitlines():
        line = line.strip().strip('"').strip("'")
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("RESEND_API_KEY="):
            line = line.split("=", 1)[1].strip().strip('"').strip("'")
        candidate = line
        break

    if not candidate:
        _fail(f"no key found in {path}")
    return candidate


def _display(path: str) -> str:
    try:
        return os.path.relpath(path, BACKEND_DIR)
    except ValueError:  # different drive on Windows
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=DEFAULT_KEY_FILE, help="file holding the API key")
    parser.add_argument(
        "--to",
        dest="recipient",
        required=True,
        help="address to send the verification message to (must be reachable)",
    )
    parser.add_argument("--from", dest="sender", default=None, help="value for EMAIL_FROM")
    parser.add_argument("--keep", action="store_true", help="keep the input file")
    args = parser.parse_args()

    key = _read_key(args.file)

    try:
        result = email_setup.install(
            key, args.sender, test_recipient=args.recipient, store_name=None
        )
    except ValueError as exc:
        _fail(f"not installed — {exc}")

    print(f"verified and wrote RESEND_API_KEY={result['key']} to backend/.env")
    print(f"sent a test message to {args.recipient} via the {result['transport']} transport")
    if result.get("from_address"):
        print(f"From: {result['from_address']}")
    print("the running app picks this up through backend/.env on its next start; the in-app")
    print("Email setup applies keys immediately.")

    if not args.keep and os.path.isfile(args.file):
        os.remove(args.file)
        print(f"removed {_display(args.file)} (the key now lives only in .env)")


if __name__ == "__main__":
    main()
