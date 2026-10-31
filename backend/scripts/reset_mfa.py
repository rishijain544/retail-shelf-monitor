"""
backend/scripts/reset_mfa.py

Clear MFA enrolment for one account.

This exists because there is no password-reset flow yet: an operator who has lost both
their authenticator device and their printed recovery codes is otherwise locked out
permanently. It is an operator script on purpose — an HTTP endpoint that removed a
second factor would let a stolen password (or session) undo MFA entirely, which is the
exact attack MFA is there to stop.

What it does for the given address:
  * mfa_enabled = false
  * the stored (encrypted) TOTP secret is deleted
  * the replay-protection step is cleared
  * every recovery code row is deleted
  * the MFA lockout is cleared

It does NOT change the password or ``email_verified``, and it never prints a secret.
On the next sign-in the account gets an ``mfa_setup_required`` token and enrols a new
authenticator.

Usage (from backend/):
    python scripts/reset_mfa.py owner@shop.com
"""

from __future__ import annotations

import sys
from pathlib import Path

# Put backend/ in sys.path so `app.*` imports resolve when run as a script.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.database import SessionLocal
from app.models.user import User
from app.services import mfa


def reset_for_email(email: str) -> int:
    """Reset MFA for `email`; returns a process exit code."""
    normalised = email.strip().lower()
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == normalised).first()
        if user is None:
            print(f"No account found for {normalised!r}.", file=sys.stderr)
            return 1

        was_enabled = bool(user.mfa_enabled)
        codes_before = mfa.count_unused_recovery_codes(db, user)
        mfa.clear_mfa(db, user)

        print(f"MFA reset for {normalised} (user id {user.id}).")
        print(f"  was enabled:        {was_enabled}")
        print(f"  recovery codes:     {codes_before} unused, now all deleted")
        print("  Next sign-in will ask this account to enrol a new authenticator.")
        print("  The password and email verification are unchanged.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in {"-h", "--help"}:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        print("Usage: python scripts/reset_mfa.py <email>", file=sys.stderr)
        return 2
    return reset_for_email(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
