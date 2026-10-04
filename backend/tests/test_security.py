"""Security tests: admin-only endpoints, OTP lockout, scout name privacy.

Uses the app's real SessionLocal (the auth middleware queries it directly),
so run with a throwaway DATABASE_URL, e.g. sqlite:///./test.db
"""

import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AllowedEmail, AuthCode, AuthSession, ScoutRoster
from app.routes import auth


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    for model in (AuthSession, AuthCode, AllowedEmail, ScoutRoster):
        s.query(model).delete()
    s.commit()
    s.close()


@pytest.fixture
def client():
    auth._rate_limit_store.clear()
    with TestClient(app) as c:
        yield c


def _session(db, email):
    token = secrets.token_hex(32)
    db.add(AuthSession(token=token, email=email,
                       expires_at=datetime.utcnow() + timedelta(hours=1)))
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _scout(db, name="Jane Doe"):
    s = ScoutRoster(name=name, scout_id="1234", password_hash=auth._hash_password("secret1"))
    db.add(s)
    db.commit()
    return s


@pytest.mark.parametrize("path", [
    "/api/scout/data", "/api/scout/data/summary", "/api/stats/",
    "/api/imports/", "/api/imports/unmatched/", "/api/houses/",
    "/api/events/", "/api/arcgis/test",
])
def test_scout_cannot_read_admin_data(client, db, path):
    headers = _session(db, f"scout:{uuid.uuid4()}")
    assert client.get(path, headers=headers).status_code == 403


def test_admin_can_read_admin_data(client, db):
    headers = _session(db, "admin")
    assert client.get("/api/events/", headers=headers).status_code == 200


def test_public_roster_shows_first_name_last_initial(client, db):
    _scout(db, "Jane Marie Doe")
    rows = client.get("/api/auth/scout-roster").json()
    assert rows[0]["name"] == "Jane D."
    assert "scout_id" not in rows[0]


def test_scout_sees_short_names_admin_sees_full(client, db):
    s = _scout(db)
    scout_rows = client.get("/api/scout/roster", headers=_session(db, f"scout:{s.id}")).json()
    admin_rows = client.get("/api/scout/roster", headers=_session(db, "admin")).json()
    assert scout_rows[0]["name"] == "Jane D." and scout_rows[0]["scout_id"] is None
    assert admin_rows[0]["name"] == "Jane Doe"


def test_short_name():
    assert auth._short_name("Jane Doe") == "Jane D."
    assert auth._short_name("Cher") == "Cher"
    assert auth._short_name("") == ""


def test_code_locked_after_too_many_wrong_guesses(client, db):
    email = "parent@example.com"
    db.add(AuthCode(email=email, code="123456",
                    expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    for _ in range(auth._MAX_CODE_ATTEMPTS):
        r = client.post("/api/auth/verify-code", json={"email": email, "code": "000000"})
        assert r.status_code == 401
    # Correct code no longer works — it was burned
    r = client.post("/api/auth/verify-code", json={"email": email, "code": "123456"})
    assert r.status_code == 401


def test_correct_code_still_logs_in(client, db):
    email = "parent@example.com"
    db.add(AuthCode(email=email, code="123456",
                    expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    client.post("/api/auth/verify-code", json={"email": email, "code": "000000"})
    r = client.post("/api/auth/verify-code", json={"email": email, "code": "123456"})
    assert r.status_code == 200 and r.json()["token"]
