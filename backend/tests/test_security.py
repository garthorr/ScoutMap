"""Security tests: admin-only endpoints, OTP lockout, scout name privacy.

Uses the app's real SessionLocal (the auth middleware queries it directly),
so run with a throwaway DATABASE_URL, e.g. sqlite:///./test.db
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.models import AllowedEmail, AuthCode, ScoutRoster
from app.routes import auth
from conftest import _session


def _scout(db, name="Jane Doe", code="12345678"):
    s = ScoutRoster(name=name, scout_id="1234", login_code=code)
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


def test_scout_list_is_not_public(client, db):
    _scout(db)
    assert client.get("/api/auth/scout-roster").status_code == 404   # old public list is gone
    assert client.get("/api/scout/roster").status_code == 401


def test_only_admins_see_roster_and_codes(client, db):
    s = _scout(db)
    assert client.get("/api/scout/roster", headers=_session(db, f"scout:{s.id}")).status_code == 403
    rows = client.get("/api/scout/roster", headers=_session(db, "admin")).json()
    assert rows[0]["name"] == "Jane Doe" and rows[0]["login_code"] == "12345678"


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


def test_allowlist_exact_and_wildcard(db):
    db.add_all([AllowedEmail(email="a@example.com"), AllowedEmail(email="*@troop.org")])
    db.commit()
    assert auth._is_email_allowed("A@Example.com", db)
    assert auth._is_email_allowed("leader@troop.org", db)
    assert not auth._is_email_allowed("x@other.com", db)


def test_scout_data_paging(client, db):
    from app.models import EventHouse, FundraiserEvent, MasterHouse, Visit
    ev = FundraiserEvent(name="Spring")
    house = MasterHouse(normalized_address="1 MAIN ST", full_address="1 Main St")
    db.add_all([ev, house])
    db.flush()
    eh = EventHouse(event_id=ev.id, house_id=house.id)
    db.add(eh)
    db.flush()
    db.add_all([Visit(event_house_id=eh.id, scout_name=f"S{i}") for i in range(5)])
    db.commit()
    headers = _session(db, "admin")
    try:
        page1 = client.get("/api/scout/data?limit=3", headers=headers).json()
        page2 = client.get("/api/scout/data?limit=3&offset=3", headers=headers).json()
        assert len(page1) == 3 and len(page2) == 2
        assert page1[0]["address"] == "1 Main St" and page1[0]["event_name"] == "Spring"
        assert not {v["id"] for v in page1} & {v["id"] for v in page2}
    finally:
        for m in (Visit, EventHouse, MasterHouse, FundraiserEvent):
            db.query(m).delete()
        db.commit()
