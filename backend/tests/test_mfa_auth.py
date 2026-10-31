"""
backend/tests/test_mfa_auth.py

The two security promises, end to end:

* an address only becomes a working account once a code delivered to it is confirmed
  (no token on signup, 403 on login until then);
* a password alone never yields a session — enrolment and then a TOTP (or one-time
  recovery) code are required, and the intermediate tokens are useless anywhere else.

Email is never really sent: the `verification_codes` fixture in conftest.py captures
`email_service.send_verification_code`.
"""
from __future__ import annotations

import time

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.email_verification_code import EmailVerificationCode
from app.models.mfa_recovery_code import MfaRecoveryCode
from app.models.user import User
from app.services import demo_account, mfa

BASE = "/api/v1"
TEST_SECRET = "test-secret-key-with-at-least-32-chars"

PASSWORD = "correct-horse"
EMAIL = "owner@shop.com"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestingSessionLocal
    app.dependency_overrides.clear()


@pytest.fixture
def client(session_factory):
    yield TestClient(app)


@pytest.fixture
def db_session(session_factory):
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


# ─── helpers ──────────────────────────────────────────────────────────────────


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signup(client, *, email: str = EMAIL, password: str = PASSWORD, **extra):
    return client.post(
        f"{BASE}/auth/signup",
        json={
            "store_name": "North",
            "store_location": "Northville",
            "email": email,
            "password": password,
            **extra,
        },
    )


def _latest_code(verification_codes: list[dict], email: str = EMAIL) -> str:
    matches = [m for m in verification_codes if m["to"] == email]
    assert matches, f"no verification email was sent to {email}"
    return matches[-1]["code"]


def _verify_email(client, verification_codes, *, email: str = EMAIL, code=None) -> str:
    """Confirm the address and return the mfa_setup token."""
    resp = client.post(
        f"{BASE}/auth/verify-email",
        json={"email": email, "code": code or _latest_code(verification_codes, email)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["setup_token"]


def _enrol(client, verification_codes, *, email: str = EMAIL, password: str = PASSWORD):
    """signup → verify → setup/start → setup/confirm. Returns (secret, confirm body)."""
    assert _signup(client, email=email, password=password).status_code == 202
    setup_token = _verify_email(client, verification_codes, email=email)

    start = client.post(f"{BASE}/auth/mfa/setup/start", headers=_bearer(setup_token))
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]

    confirm = client.post(
        f"{BASE}/auth/mfa/setup/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers=_bearer(setup_token),
    )
    assert confirm.status_code == 200, confirm.text
    return secret, confirm.json()


def _login(client, *, email: str = EMAIL, password: str = PASSWORD):
    return client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


def _totp(secret: str, steps_ahead: int = 1) -> str:
    """A code for a *future* step, so it is never a replay of the enrolment code."""
    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)


def _user(db_session, email: str = EMAIL) -> User:
    db_session.expire_all()
    return db_session.query(User).filter(User.email == email).one()


# ─── signup / verification ────────────────────────────────────────────────────


def test_unverified_account_cannot_log_in(client, verification_codes):
    """Signup alone must not produce a usable session, even with the right password."""
    assert _signup(client).status_code == 202

    resp = _login(client)
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "email_unverified"
    assert "access_token" not in body
    assert "not verified" in body["detail"].lower()


def test_correct_code_verifies_the_address(client, db_session, verification_codes):
    assert _signup(client).status_code == 202
    code = _latest_code(verification_codes)

    resp = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "email_verified"
    assert body["setup_token"]
    # A verified address still cannot sign in without enrolling a second factor.
    assert _user(db_session).email_verified is True
    assert _login(client).json()["status"] == "mfa_setup_required"


def test_verification_code_is_single_use(client, verification_codes):
    _signup(client)
    code = _latest_code(verification_codes)
    assert client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": code}
    ).status_code == 200

    again = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": code}
    )
    assert again.status_code == 400, again.text


def test_five_wrong_codes_burn_the_code(client, db_session, verification_codes):
    """After MAX_CODE_ATTEMPTS wrong guesses the (correct) code must stop working."""
    _signup(client)
    good = _latest_code(verification_codes)

    for _ in range(settings.MAX_CODE_ATTEMPTS):
        resp = client.post(
            f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": "000000"}
        )
        assert resp.status_code == 400, resp.text

    row = db_session.query(EmailVerificationCode).one()
    assert row.consumed_at is not None

    # The real code no longer works: the row was burned, not left half-alive.
    resp = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": good}
    )
    assert resp.status_code == 400, resp.text
    assert _user(db_session).email_verified is False


def test_expired_code_is_rejected(client, db_session, verification_codes, monkeypatch):
    _signup(client)
    code = _latest_code(verification_codes)

    # Move time forward past the TTL instead of sleeping for ten minutes.
    from datetime import timedelta

    row = db_session.query(EmailVerificationCode).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db_session.commit()

    resp = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": code}
    )
    assert resp.status_code == 400, resp.text
    assert _user(db_session).email_verified is False


def test_unknown_address_and_wrong_code_look_identical(client, verification_codes):
    _signup(client)
    wrong_code = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": "000000"}
    )
    unknown = client.post(
        f"{BASE}/auth/verify-email",
        json={"email": "nobody@shop.com", "code": "000000"},
    )
    assert wrong_code.status_code == unknown.status_code == 400
    assert wrong_code.json() == unknown.json()


def test_resend_cooldown_blocks_a_second_email(client, verification_codes):
    _signup(client)
    assert len(verification_codes) == 1

    resp = client.post(f"{BASE}/auth/resend-verification", json={"email": EMAIL})
    assert resp.status_code == 429, resp.text
    assert resp.json()["code"] == "resend_cooldown"
    assert len(verification_codes) == 1  # nothing was sent


def test_resend_after_cooldown_sends_a_new_code(client, db_session, verification_codes):
    from datetime import timedelta

    _signup(client)
    first = _latest_code(verification_codes)

    # Age the row past the 60s cooldown.
    row = db_session.query(EmailVerificationCode).one()
    row.created_at = utcnow() - timedelta(seconds=61)
    db_session.commit()

    resp = client.post(f"{BASE}/auth/resend-verification", json={"email": EMAIL})
    assert resp.status_code == 202, resp.text
    assert len(verification_codes) == 2

    # The newest code works; the replaced one does not.
    newest = _latest_code(verification_codes)
    assert newest != first
    stale = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": first}
    )
    assert stale.status_code == 400, stale.text
    fresh = client.post(
        f"{BASE}/auth/verify-email", json={"email": EMAIL, "code": newest}
    )
    assert fresh.status_code == 200, fresh.text


def test_resend_for_unknown_address_does_not_reveal_anything(client, verification_codes):
    resp = client.post(
        f"{BASE}/auth/resend-verification", json={"email": "nobody@shop.com"}
    )
    assert resp.status_code == 202, resp.text
    assert verification_codes == []


# ─── login → challenge/setup ──────────────────────────────────────────────────


def test_login_without_enrolment_returns_only_a_setup_token(client, verification_codes):
    _signup(client)
    _verify_email(client, verification_codes)

    resp = _login(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "mfa_setup_required"
    assert body["setup_token"]
    assert body["expires_in"] > 0
    assert "access_token" not in body


def _protected_requests(client, token: str):
    """Every private route, with a token that must not work on any of them."""
    headers = _bearer(token)
    return {
        "reports": client.get(f"{BASE}/reports", headers=headers),
        "alerts": client.get(f"{BASE}/alerts", headers=headers),
        "cameras": client.get(f"{BASE}/cameras", headers=headers),
        "me": client.get(f"{BASE}/auth/me", headers=headers),
        "upload": client.post(
            f"{BASE}/captures/upload",
            headers=headers,
            data={"camera_id": "1"},
            files={"image": ("shelf.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
        ),
    }


def test_setup_token_is_rejected_by_every_protected_endpoint(
    client, verification_codes
):
    _signup(client)
    setup_token = _verify_email(client, verification_codes)

    for name, resp in _protected_requests(client, setup_token).items():
        assert resp.status_code == 401, f"{name} accepted a setup token: {resp.text}"


def test_challenge_token_is_rejected_by_every_protected_endpoint(client, verification_codes):
    _signup(client)
    _enrol_after_signup(client, verification_codes)

    challenge = _login(client).json()
    assert challenge["status"] == "mfa_required"

    for name, resp in _protected_requests(client, challenge["challenge_token"]).items():
        assert resp.status_code == 401, f"{name} accepted a challenge token: {resp.text}"


def _enrol_after_signup(client, verification_codes, *, email: str = EMAIL, password: str = PASSWORD):
    """Continue an existing (already signed-up) account through enrolment."""
    setup_token = _verify_email(client, verification_codes, email=email)
    start = client.post(f"{BASE}/auth/mfa/setup/start", headers=_bearer(setup_token))
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    confirm = client.post(
        f"{BASE}/auth/mfa/setup/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers=_bearer(setup_token),
    )
    assert confirm.status_code == 200, confirm.text
    return secret, confirm.json()


# ─── happy path ───────────────────────────────────────────────────────────────


def test_full_signup_to_access_flow(client, db_session, verification_codes):
    secret, confirmed = _enrol(client, verification_codes)
    assert confirmed["access_token"]
    assert len(confirmed["recovery_codes"]) == 10
    assert len({c for c in confirmed["recovery_codes"]}) == 10

    login = _login(client)
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["status"] == "mfa_required"
    assert "access_token" not in body

    verified = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": body["challenge_token"], "code": _totp(secret)},
    )
    assert verified.status_code == 200, verified.text
    access = verified.json()["access_token"]
    assert access

    me = client.get(f"{BASE}/auth/me", headers=_bearer(access))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == EMAIL
    assert me.json()["mfa_enabled"] is True

    # And the freshly minted access token is a real one.
    assert client.get(f"{BASE}/reports", headers=_bearer(access)).status_code == 200


def test_setup_start_returns_a_usable_otpauth_uri(client, verification_codes):
    _signup(client)
    setup_token = _verify_email(client, verification_codes)

    resp = client.post(f"{BASE}/auth/mfa/setup/start", headers=_bearer(setup_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert settings.MFA_ISSUER_NAME.replace(" ", "") in body["otpauth_uri"]
    assert body["issuer"] == settings.MFA_ISSUER_NAME
    assert body["account"] == EMAIL
    assert pyotp.TOTP(body["secret"]).now()  # base32 and usable


def test_setup_start_replaces_the_pending_secret(client, verification_codes):
    _signup(client)
    setup_token = _verify_email(client, verification_codes)

    first = client.post(
        f"{BASE}/auth/mfa/setup/start", headers=_bearer(setup_token)
    ).json()["secret"]
    second = client.post(
        f"{BASE}/auth/mfa/setup/start", headers=_bearer(setup_token)
    ).json()["secret"]
    assert first != second

    # Confirming with the replaced secret must fail: only the newest is live.
    rejected = client.post(
        f"{BASE}/auth/mfa/setup/confirm",
        json={"code": pyotp.TOTP(first).now()},
        headers=_bearer(setup_token),
    )
    assert rejected.status_code == 400, rejected.text

    accepted = client.post(
        f"{BASE}/auth/mfa/setup/confirm",
        json={"code": pyotp.TOTP(second).now()},
        headers=_bearer(setup_token),
    )
    assert accepted.status_code == 200, accepted.text


# ─── replay ───────────────────────────────────────────────────────────────────


def test_totp_code_cannot_be_replayed(client, verification_codes):
    """The same code presented twice must be rejected the second time."""
    secret, _ = _enrol(client, verification_codes)
    challenge = _login(client).json()["challenge_token"]

    code = _totp(secret)
    first = client.post(
        f"{BASE}/auth/mfa/verify", json={"challenge_token": challenge, "code": code}
    )
    assert first.status_code == 200, first.text

    second_challenge = _login(client).json()["challenge_token"]
    second = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": second_challenge, "code": code},
    )
    assert second.status_code == 401, second.text
    assert "access_token" not in second.json()


def test_recovery_code_works_once(client, verification_codes):
    secret, confirmed = _enrol(client, verification_codes)
    recovery_code = confirmed["recovery_codes"][0]

    challenge = _login(client).json()["challenge_token"]
    first = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": challenge, "code": recovery_code},
    )
    assert first.status_code == 200, first.text
    assert first.json()["access_token"]

    challenge = _login(client).json()["challenge_token"]
    second = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": challenge, "code": recovery_code},
    )
    assert second.status_code == 401, second.text

    # The account is still usable with the authenticator afterwards.
    challenge = _login(client).json()["challenge_token"]
    third = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": challenge, "code": _totp(secret, steps_ahead=1)},
    )
    assert third.status_code == 200, third.text


def test_recovery_code_accepts_any_case_or_separator(client, verification_codes):
    _, confirmed = _enrol(client, verification_codes)
    recovery_code = confirmed["recovery_codes"][1]
    sloppy = recovery_code.replace("-", " ").upper()

    challenge = _login(client).json()["challenge_token"]
    resp = client.post(
        f"{BASE}/auth/mfa/verify", json={"challenge_token": challenge, "code": sloppy}
    )
    assert resp.status_code == 200, resp.text


# ─── lockout ──────────────────────────────────────────────────────────────────


def test_mfa_locks_out_after_five_wrong_codes(client, db_session, verification_codes):
    _enrol(client, verification_codes)

    for _ in range(settings.MAX_CODE_ATTEMPTS):
        challenge = _login(client).json()["challenge_token"]
        resp = client.post(
            f"{BASE}/auth/mfa/verify",
            json={"challenge_token": challenge, "code": "000000"},
        )
        assert resp.status_code == 401, resp.text

    user = _user(db_session)
    assert user.mfa_locked_until is not None

    # Even a correct code is refused while the lockout is live.
    challenge = _login(client).json()["challenge_token"]
    locked = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": challenge, "code": _totp(_secret_of(db_session))},
    )
    assert locked.status_code == 429, locked.text
    assert locked.json()["code"] == "mfa_locked"


def _secret_of(db_session) -> str:
    """The enrolled account's TOTP secret, as the server would read it."""
    return mfa.read_secret(_user(db_session))


def test_mfa_lockout_expires_and_a_valid_code_works_again(
    client, db_session, verification_codes
):
    from datetime import timedelta

    secret, _ = _enrol(client, verification_codes)

    for _ in range(settings.MAX_CODE_ATTEMPTS):
        challenge = _login(client).json()["challenge_token"]
        client.post(
            f"{BASE}/auth/mfa/verify",
            json={"challenge_token": challenge, "code": "000000"},
        )
    assert _user(db_session).mfa_locked_until is not None

    # The lockout passes (no sleeping for 15 minutes in a unit test).
    user = _user(db_session)
    user.mfa_locked_until = utcnow() - timedelta(seconds=1)
    db_session.commit()

    challenge = _login(client).json()["challenge_token"]
    resp = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": challenge, "code": _totp(secret, steps_ahead=1)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


# ─── password lockout ────────────────────────────────────────────────────────


def test_login_locks_out_after_repeated_bad_passwords(client, db_session, verification_codes):
    from datetime import timedelta

    _enrol(client, verification_codes)

    for _ in range(settings.MAX_CODE_ATTEMPTS):
        resp = _login(client, password="wrong-password")
        assert resp.status_code == 401, resp.text
        # Same message whether the address exists or not.
        assert resp.json()["detail"] == "Invalid email or password."

    user = _user(db_session)
    assert user.login_locked_until is not None

    locked = _login(client)  # correct password, but the account is locked
    assert locked.status_code == 429, locked.text
    assert locked.json()["code"] == "account_locked"

    user.login_locked_until = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert _login(client).status_code == 200


def test_unknown_email_and_wrong_password_are_indistinguishable(client, verification_codes):
    _enrol(client, verification_codes)

    unknown = client.post(
        f"{BASE}/auth/login",
        json={"email": "nobody@shop.com", "password": "correct-horse"},
    )
    wrong = _login(client, password="wrong-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


# ─── recovery-code regeneration ──────────────────────────────────────────────


def test_regenerate_recovery_codes_needs_password_and_a_current_code(
    client, verification_codes
):
    secret, confirmed = _enrol(client, verification_codes)
    access = confirmed["access_token"]
    old_codes = confirmed["recovery_codes"]

    bad_password = client.post(
        f"{BASE}/auth/mfa/recovery-codes/regenerate",
        json={"password": "not-it", "code": _totp(secret, steps_ahead=1)},
        headers=_bearer(access),
    )
    assert bad_password.status_code == 401, bad_password.text

    bad_code = client.post(
        f"{BASE}/auth/mfa/recovery-codes/regenerate",
        json={"password": PASSWORD, "code": "000000"},
        headers=_bearer(access),
    )
    assert bad_code.status_code == 401, bad_code.text

    ok = client.post(
        f"{BASE}/auth/mfa/recovery-codes/regenerate",
        json={"password": PASSWORD, "code": _totp(secret, steps_ahead=1)},
        headers=_bearer(access),
    )
    assert ok.status_code == 200, ok.text
    new_codes = ok.json()["recovery_codes"]
    assert len(new_codes) == 10
    assert set(new_codes).isdisjoint(old_codes)

    # The old codes stop working immediately.
    challenge = _login(client).json()["challenge_token"]
    stale = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": challenge, "code": old_codes[0]},
    )
    assert stale.status_code == 401, stale.text


# ─── demo account exemption ──────────────────────────────────────────────────


def test_exempt_demo_account_signs_in_with_password_only(client, db_session):
    """The published one-click demo login keeps working with no extra factor."""
    assert demo_account.ensure_demo_account(db_session) is True

    resp = _login(
        client, email=demo_account.DEMO_EMAIL, password=demo_account.DEMO_PASSWORD
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["email"] == demo_account.DEMO_EMAIL

    me = client.get(f"{BASE}/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["mfa_enabled"] is False


def test_demo_account_is_repaired_when_it_already_exists(
    client, db_session, verification_codes
):
    """An account created before MFA existed must not wall off the demo login."""
    _signup(client, email=demo_account.DEMO_EMAIL, password=demo_account.DEMO_PASSWORD)
    assert _login(
        client, email=demo_account.DEMO_EMAIL, password=demo_account.DEMO_PASSWORD
    ).status_code == 403

    # The boot-time/seed repair makes the published credentials work again.
    assert demo_account.ensure_demo_account(db_session) is False
    assert _user(db_session, demo_account.DEMO_EMAIL).email_verified is True
    assert _user(db_session, demo_account.DEMO_EMAIL).mfa_exempt is True

    resp = _login(
        client, email=demo_account.DEMO_EMAIL, password=demo_account.DEMO_PASSWORD
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_no_api_can_set_mfa_exempt(client, db_session, verification_codes):
    """mfa_exempt is a seed/operator flag, never a request field."""
    resp = _signup(client, mfa_exempt=True)
    assert resp.status_code == 202, resp.text
    assert _user(db_session).mfa_exempt is False

    # And there is no endpoint that disables MFA or edits the flag.
    paths = client.get(f"{BASE}/openapi.json").json()["paths"]
    assert not [p for p in paths if "disable" in p or "mfa_exempt" in p]


# ─── at-rest encryption ─────────────────────────────────────────────────────


def test_stored_mfa_secret_is_encrypted(client, db_session, verification_codes):
    secret, _ = _enrol(client, verification_codes)

    raw = db_session.execute(
        text("SELECT mfa_secret_encrypted FROM users WHERE email = :email"),
        {"email": EMAIL},
    ).scalar_one()

    assert raw
    assert raw != secret
    assert secret not in raw
    # …and it really is readable by the service (encryption, not lossy hashing).
    assert mfa.read_secret(_user(db_session)) == secret


def test_encrypted_secret_is_not_returned_by_any_endpoint(client, verification_codes):
    _, confirmed = _enrol(client, verification_codes)
    access = confirmed["access_token"]

    me = client.get(f"{BASE}/auth/me", headers=_bearer(access))
    assert me.status_code == 200
    assert "mfa_secret" not in me.text
    assert "mfa_secret_encrypted" not in me.text

    login = _login(client)
    assert "mfa_secret" not in login.text


def test_recovery_codes_are_not_stored_in_plaintext(client, db_session, verification_codes):
    _, confirmed = _enrol(client, verification_codes)
    codes = confirmed["recovery_codes"]

    rows = db_session.query(MfaRecoveryCode).all()
    assert len(rows) == 10
    stored = {row.code_hash for row in rows}
    for code in codes:
        assert code not in stored
        assert code.replace("-", "") not in stored
    # bcrypt digests, as specified.
    assert all(h.startswith("$2") for h in stored)


def test_access_token_from_before_purposes_existed_is_rejected(client, db_session):
    """A legacy token with no `purpose` claim must not open a session."""
    user = User(
        email="legacy@shop.com",
        hashed_password="x",
        store_id=_store_id(db_session),
        email_verified=True,
    )
    db_session.add(user)
    db_session.commit()

    legacy = jwt.encode(
        {"sub": str(user.id), "exp": time.time() + 600},
        TEST_SECRET,
        algorithm="HS256",
    )
    resp = client.get(f"{BASE}/auth/me", headers=_bearer(legacy))
    assert resp.status_code == 401, resp.text


def _store_id(db_session) -> int:
    from app.models.store import Store

    store = Store(name="Legacy", location=None)
    db_session.add(store)
    db_session.flush()
    return store.id


def test_challenge_token_cannot_start_enrolment(client, verification_codes):
    """Purpose separation holds in both directions."""
    secret, _ = _enrol(client, verification_codes)
    challenge = _login(client).json()["challenge_token"]

    resp = client.post(f"{BASE}/auth/mfa/setup/start", headers=_bearer(challenge))
    assert resp.status_code == 401, resp.text


def test_setup_token_cannot_finish_an_mfa_challenge(client, verification_codes):
    _signup(client)
    setup_token = _verify_email(client, verification_codes)

    resp = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": setup_token, "code": "123456"},
    )
    assert resp.status_code == 401, resp.text


def test_expired_challenge_token_is_rejected(client, db_session, verification_codes):
    """Challenge tokens live 5 minutes; an expired one must not mint a session."""
    from datetime import timedelta

    from app.core.security import PURPOSE_MFA_CHALLENGE, create_token

    secret, _ = _enrol(client, verification_codes)
    expired = create_token(
        _user(db_session).id,
        purpose=PURPOSE_MFA_CHALLENGE,
        expires_delta=timedelta(seconds=-1),
    )

    resp = client.post(
        f"{BASE}/auth/mfa/verify",
        json={"challenge_token": expired, "code": _totp(secret)},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "mfa_challenge_invalid"
