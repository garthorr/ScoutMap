"""Session/credential storage, cookies, cross-site blocking, headers, uploads."""

import logging

from app.config import settings
from app.main import _SESSION_CACHE
from app.models import AllowedEmail, AuthCode, AuthSession, ScoutRoster
from app.routes import auth

from conftest import _session


def _admin_login(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "pw-for-tests")
    return client.post("/api/auth/admin-login", json={"password": "pw-for-tests"})


def test_login_sets_httponly_cookie_and_stores_only_a_hash(client, db, monkeypatch):
    r = _admin_login(client, monkeypatch)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "scoutmap_token=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()

    token = r.json()["token"]
    stored = [s.token for s in db.query(AuthSession).all()]
    assert token not in stored and auth.hash_token(token) in stored


def test_cookie_alone_authenticates_and_logout_clears_it(client, db, monkeypatch):
    _admin_login(client, monkeypatch)  # TestClient keeps the cookie
    assert client.get("/api/auth/me").status_code == 200
    r = client.post("/api/auth/logout")
    assert r.status_code == 200 and 'scoutmap_token=""' in r.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 401
    assert db.query(AuthSession).count() == 0


def test_email_code_is_stored_hashed(client, db):
    db.add(AllowedEmail(email="leader@example.com"))
    db.commit()
    assert client.post("/api/auth/request-code", json={"email": "leader@example.com"}).status_code == 200
    stored = db.query(AuthCode).filter(AuthCode.used == False).one().code  # noqa: E712
    assert len(stored) > 6 and ":" in stored


def test_smtp_failure_is_reported_and_code_not_logged(client, db, monkeypatch, caplog):
    db.add(AllowedEmail(email="leader@example.com"))
    db.commit()
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")

    sent_codes = []

    def broken_smtp(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(auth, "_generate_code", lambda length=6: sent_codes.append("424242") or "424242")
    monkeypatch.setattr(auth.smtplib, "SMTP", broken_smtp)
    with caplog.at_level(logging.DEBUG):
        r = client.post("/api/auth/request-code", json={"email": "leader@example.com"})
    assert r.status_code == 502
    assert sent_codes and "424242" not in caplog.text


def test_cross_site_post_with_cookie_is_blocked(client, db, monkeypatch):
    _admin_login(client, monkeypatch)
    r = client.post("/api/events/", json={"name": "Evil"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/events/", json={"name": "Fine"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_security_headers_and_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    page = client.get("/")
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]


def test_import_upload_size_cap(client, db, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = b"a,b\n" * (300 * 1024)  # ~1.2 MB
    r = client.post("/api/imports/", headers=_session(db, "admin"),
                    data={"source_name": "dcad"}, files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413


def test_roster_upload_size_cap(client, db):
    big = b"name\n" + b"x" * (5 * 1024 * 1024 + 10)
    r = client.post("/api/scout/roster/import", headers=_session(db, "admin"),
                    files={"file": ("roster.csv", big, "text/csv")})
    assert r.status_code == 413


def test_new_scout_code_signs_out_cached_session(client, db):
    s = ScoutRoster(name="Jane", login_code="12345678")
    db.add(s)
    db.commit()
    scout_headers = _session(db, f"scout:{s.id}")
    assert client.get("/api/auth/me", headers=scout_headers).status_code == 200  # now cached
    assert _SESSION_CACHE

    r = client.post(f"/api/auth/scout-code/{s.id}/regenerate", headers=_session(db, "admin"))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=scout_headers).status_code == 401
