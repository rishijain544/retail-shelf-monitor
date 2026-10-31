"""The one-click demo account advertised on the sign-in page.

The frontend shows ``demo@shelfmonitor.test / demo-2026-shelf`` and a "Try Demo
Account" button, but nothing created that row on a deployed database: `scripts/seed.py`
holds the demo account *and* five sample captures, and it is a developer script that
never runs in a container. A fresh deployment therefore advertised credentials that
answered "Incorrect email or password." with a 401.

`ensure_demo_account` creates only the account and its store, so the advertised login
works anywhere. Sample captures stay a developer concern: they need Pillow to render
placeholder images, which the API image does not install.

This is a known-password account by design (it is published in the UI), so it is gated
behind `SEED_DEMO_ACCOUNT` and stays off unless a deployment asks for it.

The account is also ``mfa_exempt``: there is no mailbox to send a verification code to
and no authenticator the public could hold, so requiring either factor would make the
advertised one-click login impossible. ``mfa_exempt`` is never writable through the
API — this module and scripts/reset_mfa.py are its only writers — and the demo row is
kept exempt even when it already exists, so upgrading a deployment does not turn the
button into a "set up an authenticator" wall.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.store import Store
from app.models.user import User

logger = logging.getLogger(__name__)

# Keep in sync with frontend/src/demo.ts (VITE_DEMO_EMAIL / VITE_DEMO_PASSWORD, which
# default to these same values).
DEMO_EMAIL = "demo@shelfmonitor.test"
DEMO_PASSWORD = "demo-2026-shelf"
DEMO_STORE_NAME = "Demo Store"


def ensure_demo_account(db: Session) -> bool:
    """Create the demo store and account if missing. True when something was inserted.

    Idempotent: a re-deploy finds the existing row and changes nothing, so the demo
    account keeps its id and any captures uploaded against it.
    """
    existing = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if existing is not None:
        # Repair accounts created before MFA existed: a demo row that is unverified
        # or non-exempt would answer "verification required" / "set up MFA" instead of
        # signing the visitor in, which is the one behaviour this account promises.
        repaired = False
        if not existing.email_verified:
            existing.email_verified = True
            repaired = True
        if not existing.mfa_exempt:
            existing.mfa_exempt = True
            repaired = True
        if existing.mfa_enabled:
            existing.mfa_enabled = False
            existing.mfa_secret_encrypted = None
            existing.mfa_last_used_step = None
            repaired = True
        if repaired:
            db.commit()
            logger.info("Restored the demo account %s to verified + MFA-exempt.", DEMO_EMAIL)
        return False

    store = db.query(Store).filter(Store.name == DEMO_STORE_NAME).first()
    if store is None:
        store = Store(name=DEMO_STORE_NAME, location=None)
        db.add(store)
        db.flush()

    db.add(
        User(
            email=DEMO_EMAIL,
            hashed_password=hash_password(DEMO_PASSWORD),
            store_id=store.id,
            # The two exemptions that make the published credentials a working login:
            # the address cannot receive mail and the public has no second factor.
            email_verified=True,
            mfa_exempt=True,
            mfa_enabled=False,
        )
    )
    db.commit()
    logger.info("Created the demo account %s in store %r", DEMO_EMAIL, store.name)
    return True
