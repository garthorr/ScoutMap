"""Authentication endpoints – email OTP login flow + scout password login."""

import hashlib
import logging
import os
import secrets
import uuid
import smtplib
from collections import Counter
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fnmatch import fnmatch
from random import SystemRandom

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AllowedEmail, AuthCode, AuthSession, ScoutRoster

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
_rng = SystemRandom()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RequestCodeBody(BaseModel):
    email: str


class VerifyCodeBody(BaseModel):
    email: str
    code: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_email_allowed(email: str, db: Session) -> bool:
    """Check if email matches any allowed pattern in the database."""
    email = email.strip().lower()
    # Exact match uses the index; only wildcard patterns need checking in Python
    if db.query(AllowedEmail.id).filter(AllowedEmail.email == email).first():
        return True
    patterns = db.query(AllowedEmail.email).filter(AllowedEmail.email.contains("*")).all()
    return any(fnmatch(email, row.email.strip().lower()) for row in patterns)


def _short_name(name: str, letters: int = 1) -> str:
    """'Jane Doe' -> 'Jane D.' (protects minors' full names)."""
    parts = (name or "").split()
    if len(parts) < 2:
        return name or ""
    return f"{parts[0]} {parts[-1][:letters].capitalize()}."


def _short_names(names: list[str]) -> list[str]:
    """Short names for a list, kept distinguishable.

    Two "Jack S." become "Jack Sm." and "Jack St."; identical names
    get a number: "Sam L. (1)", "Sam L. (2)".
    """
    # Lengthen the last-name part only between *different* full names
    keys = [" ".join((n or "").lower().split()) for n in names]
    original = dict(zip(keys, names))
    short = {k: _short_name(original[k]) for k in original}
    for letters in range(2, 6):
        counts = Counter(short.values())
        if all(c == 1 for c in counts.values()):
            break
        short = {k: _short_name(original[k], letters) if counts[v] > 1 else v for k, v in short.items()}

    # Number whatever still looks the same
    result = [short[k] for k in keys]
    totals, seen, out = Counter(result), Counter(), []
    for r in result:
        seen[r] += 1
        out.append(f"{r} ({seen[r]})" if totals[r] > 1 else r)
    return out


def _generate_code() -> str:
    return "".join(str(_rng.randint(0, 9)) for _ in range(6))


def _send_code_email(email: str, code: str):
    """Send the OTP code via SMTP, or log it if SMTP is not configured."""
    subject = f"ScoutMap Login Code: {code}"
    body = (
        f"Your ScoutMap verification code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in {settings.auth_code_expiry_minutes} minutes.\n\n"
        f"If you did not request this, ignore this email."
    )

    if not settings.smtp_host:
        logger.warning("SMTP not configured — login code for %s: %s", email, code)
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = email

    try:
        if settings.smtp_use_tls:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, [email], msg.as_string())
        server.quit()
        logger.info("Sent login code to %s", email)
    except Exception:
        logger.exception("Failed to send email to %s — code: %s", email, code)


# ---------------------------------------------------------------------------
# Auth dependency (used by other routes)
# ---------------------------------------------------------------------------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> str:
    """Extract and validate session token. Returns the user's email.

    The auth middleware already validates the token and caches the email
    on request.state — use that to avoid a redundant DB query.
    """
    # Fast path: middleware already validated and stored the email
    email = getattr(request.state, "user_email", None)
    if email:
        return email

    token = None

    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # Fallback to cookie
    if not token:
        token = request.cookies.get("scoutmap_token")

    if not token:
        raise HTTPException(401, "Not authenticated")

    session = db.query(AuthSession).filter(
        AuthSession.token == token,
        AuthSession.expires_at > datetime.utcnow(),
    ).first()
    if not session:
        raise HTTPException(401, "Session expired or invalid")

    return session.email


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Like get_current_user but rejects scout sessions.

    Admin sessions have email="admin" or a real email address.
    Scout sessions have email="scout:{uuid}".
    """
    email = get_current_user(request, db)
    if email.startswith("scout:"):
        raise HTTPException(403, "Admin access required")
    return email


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter for auth endpoints
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = {}  # key -> list of timestamps
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 10      # max attempts per window


_rate_limit_last_cleanup = 0.0
_MAX_CODE_ATTEMPTS = 5    # wrong guesses before a login code is cancelled


def _check_rate_limit(key: str):
    """Raise 429 if too many attempts for key within the window."""
    import time
    global _rate_limit_last_cleanup
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW

    # Periodic cleanup: evict stale keys every 10 minutes
    if now - _rate_limit_last_cleanup > 600:
        stale = [k for k, v in _rate_limit_store.items() if not v or v[-1] < window_start]
        for k in stale:
            del _rate_limit_store[k]
        _rate_limit_last_cleanup = now

    attempts = _rate_limit_store.get(key, [])
    attempts = [t for t in attempts if t > window_start]
    if len(attempts) >= _RATE_LIMIT_MAX:
        raise HTTPException(429, "Too many attempts. Please try again later.")
    attempts.append(now)
    _rate_limit_store[key] = attempts


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/request-code")
def request_code(body: RequestCodeBody, request: Request, db: Session = Depends(get_db)):
    """Send a 6-digit login code to the given email if it's allowed."""
    _check_rate_limit(f"code:{request.client.host}")
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email address")

    _check_rate_limit(f"code-email:{email}")

    if not _is_email_allowed(email, db):
        # Don't reveal whether email is allowed — always say "code sent"
        # but log the rejection
        logger.info("Login attempt from non-allowed email: %s", email)
        return {"ok": True, "message": "If this email is authorized, a code has been sent."}

    # Invalidate previous unused codes for this email
    db.query(AuthCode).filter(
        AuthCode.email == email,
        AuthCode.used == False,  # noqa: E712
    ).update({"used": True})

    code = _generate_code()
    auth_code = AuthCode(
        email=email,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=settings.auth_code_expiry_minutes),
    )
    db.add(auth_code)
    db.commit()

    _send_code_email(email, code)
    return {"ok": True, "message": "If this email is authorized, a code has been sent."}


@router.post("/verify-code")
def verify_code(body: VerifyCodeBody, request: Request, db: Session = Depends(get_db)):
    """Verify the OTP code and create a session."""
    _check_rate_limit(f"verify:{request.client.host}")
    email = body.email.strip().lower()
    code = body.code.strip()

    # Only one active code exists per email (older ones are marked used)
    auth_code = db.query(AuthCode).filter(
        AuthCode.email == email,
        AuthCode.used == False,  # noqa: E712
        AuthCode.expires_at > datetime.utcnow(),
    ).order_by(AuthCode.created_at.desc()).first()

    if not auth_code:
        raise HTTPException(401, "Invalid or expired code")

    if not secrets.compare_digest(auth_code.code, code):
        # Burn the code after too many wrong guesses to stop brute force
        auth_code.failed_attempts = (auth_code.failed_attempts or 0) + 1
        if auth_code.failed_attempts >= _MAX_CODE_ATTEMPTS:
            auth_code.used = True
        db.commit()
        raise HTTPException(401, "Invalid or expired code")

    auth_code.used = True

    # Create session
    token = secrets.token_hex(32)
    session = AuthSession(
        token=token,
        email=email,
        expires_at=datetime.utcnow() + timedelta(hours=settings.session_expiry_hours),
    )
    db.add(session)
    db.commit()

    return {"ok": True, "token": token, "email": email}


# ---------------------------------------------------------------------------
# Admin password login (bypasses email OTP, set via ADMIN_PASSWORD env var)
# ---------------------------------------------------------------------------
class AdminLoginBody(BaseModel):
    password: str


@router.post("/admin-login")
def admin_login(body: AdminLoginBody, request: Request, db: Session = Depends(get_db)):
    """Authenticate with the master admin password."""
    _check_rate_limit(f"admin:{request.client.host}")
    if not settings.admin_password:
        raise HTTPException(403, "Admin password login is not configured")

    if not secrets.compare_digest(body.password, settings.admin_password):
        raise HTTPException(401, "Incorrect password")

    token = secrets.token_hex(32)
    session = AuthSession(
        token=token,
        email="admin",
        expires_at=datetime.utcnow() + timedelta(hours=settings.session_expiry_hours),
    )
    db.add(session)
    db.commit()

    return {"ok": True, "token": token, "email": "admin"}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """Invalidate the current session."""
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("scoutmap_token")
    if token:
        db.query(AuthSession).filter(AuthSession.token == token).delete()
        db.commit()
        from app.main import invalidate_session_cache
        invalidate_session_cache(token)
    return {"ok": True}


@router.get("/me")
def auth_me(email: str = Depends(get_current_user)):
    """Return the current user's email (for UI display)."""
    return {"email": email}


# ---------------------------------------------------------------------------
# Allowed emails management (requires auth)
# ---------------------------------------------------------------------------
class AllowedEmailBody(BaseModel):
    email: str


@router.get("/allowed-emails")
def list_allowed_emails(
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(AllowedEmail).order_by(AllowedEmail.email).all()
    return [{"id": str(r.id), "email": r.email, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/allowed-emails")
def add_allowed_email(
    body: AllowedEmailBody,
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    normalized = body.email.strip().lower()
    existing = db.query(AllowedEmail).filter(AllowedEmail.email == normalized).first()
    if existing:
        raise HTTPException(409, "Email already in allowlist")
    row = AllowedEmail(email=normalized)
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id), "email": normalized}


@router.delete("/allowed-emails/{email_id}")
def remove_allowed_email(
    email_id: str,
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(AllowedEmail).filter(AllowedEmail.id == email_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Password hashing helpers (PBKDF2 — stdlib, no extra dependency)
# ---------------------------------------------------------------------------
def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return salt.hex() + ":" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Scout password login (no email required)
# ---------------------------------------------------------------------------
class ScoutLoginBody(BaseModel):
    scout_id: uuid.UUID  # roster row id
    password: str


@router.get("/scout-roster")
def public_scout_roster(db: Session = Depends(get_db)):
    """Public list of active scouts with passwords set (for login dropdown)."""
    scouts = db.query(ScoutRoster).filter(
        ScoutRoster.active == True,  # noqa: E712
        ScoutRoster.password_hash.isnot(None),
    ).order_by(ScoutRoster.name).all()
    names = _short_names([s.name for s in scouts])
    return [{"id": str(s.id), "name": n} for s, n in zip(scouts, names)]


@router.post("/scout-login")
def scout_login(body: ScoutLoginBody, request: Request, db: Session = Depends(get_db)):
    """Authenticate a scout by roster ID + password. Returns a session token."""
    _check_rate_limit(f"scout:{request.client.host}")
    # Per-scout limit too, so a 6-digit password can't be guessed from many IPs
    _check_rate_limit(f"scout-id:{body.scout_id}")
    scout = db.query(ScoutRoster).filter(
        ScoutRoster.id == body.scout_id,
        ScoutRoster.active == True,  # noqa: E712
    ).first()

    if not scout or not scout.password_hash:
        raise HTTPException(401, "Invalid scout or password not set")

    if not _verify_password(body.password, scout.password_hash):
        raise HTTPException(401, "Incorrect password")

    token = secrets.token_hex(32)
    session = AuthSession(
        token=token,
        email=f"scout:{scout.id}",  # tag session as scout-type
        expires_at=datetime.utcnow() + timedelta(hours=settings.session_expiry_hours),
    )
    db.add(session)
    db.commit()

    return {
        "ok": True,
        "token": token,
        "scout_name": scout.name,
        "scout_id": scout.scout_id or "",
        "roster_id": str(scout.id),
    }


# ---------------------------------------------------------------------------
# Scout passwords: always 6 random digits, generated by the server.
# Only the hash is stored, so a password is shown once — when it's made.
# ---------------------------------------------------------------------------
def new_scout_password(scout: ScoutRoster, db: Session) -> str:
    """Give a scout a fresh 6-digit password and sign out their old sessions."""
    password = _generate_code()
    scout.password_hash = _hash_password(password)
    db.query(AuthSession).filter(AuthSession.email == f"scout:{scout.id}").delete()
    return password


@router.post("/scout-password/{roster_id}/regenerate")
def regenerate_scout_password(
    roster_id: uuid.UUID,
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin replaces a scout's password with a new random one."""
    scout = db.query(ScoutRoster).filter(ScoutRoster.id == roster_id).first()
    if not scout:
        raise HTTPException(404, "Scout not found")
    password = new_scout_password(scout, db)
    db.commit()
    return {"name": scout.name, "password": password}


@router.post("/scout-passwords/generate-missing")
def generate_missing_scout_passwords(
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Give every scout who has no password a new one."""
    scouts = db.query(ScoutRoster).filter(ScoutRoster.password_hash.is_(None)).order_by(ScoutRoster.name).all()
    result = [{"name": s.name, "password": new_scout_password(s, db)} for s in scouts]
    db.commit()
    return {"passwords": result}
