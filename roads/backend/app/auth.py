"""Officials login: users with roles, cookie sessions, and server-side access rules.

Who can do what (enforced here for every request, so the browser cannot bypass it):
  * everyone (no login): read everything (map, defects, reports), report a pothole
    (POST /api/inspections) and run a live scan; both are rate-limited per IP against spam
  * officer + admin: review detections, change workflow, auto-approve, export training data
  * crew + officer + admin: mark a defect repaired / repaired-by, plan the repair route
  * admin: manage users

Passwords are stored as PBKDF2-SHA256 hashes with a per-user salt. A session is a random token in an
HttpOnly cookie; only its SHA-256 is stored. On first start, if there are no users, three demo
accounts are created with random passwords written to backend/data/demo_accounts.txt (git-ignored).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "rs_session"
SESSION_HOURS = 12
ROLES = ("admin", "officer", "crew")
ROLE_LABEL = {"admin": "Administrator", "officer": "Ward officer", "crew": "Repair crew"}
PBKDF2_ROUNDS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

# (method, path regex) -> roles allowed. Anything not listed here is public.
RULES = [
    ("POST", r"^/api/observations/\d+/review$", {"officer", "admin"}),
    ("POST", r"^/api/review/auto_approve$", {"officer", "admin"}),
    ("PATCH", r"^/api/defects/\d+/workflow$", {"officer", "admin"}),
    ("GET", r"^/api/export/corrections$", {"officer", "admin"}),
    ("POST", r"^/api/defects/\d+/repair$", {"crew", "officer", "admin"}),
    ("POST", r"^/api/defects/\d+/repaired_by$", {"crew", "officer", "admin"}),
    ("POST", r"^/api/crew/route$", {"crew", "officer", "admin"}),
    ("GET", r"^/api/auth/users$", {"admin"}),
    ("POST", r"^/api/auth/users$", {"admin"}),
    ("PATCH", r"^/api/auth/users/\d+$", {"admin"}),
]
RULES = [(m, re.compile(p), roles) for m, p, roles in RULES]
# public but rate-limited: (method, path regex, max requests, per seconds)
LIMITS = [
    ("POST", re.compile(r"^/api/inspections$"), 30, 3600),
    ("POST", re.compile(r"^/api/live/start$"), 20, 3600),
    ("POST", re.compile(r"^/api/auth/login$"), 10, 300),
]
_hits: dict[tuple, deque] = defaultdict(deque)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, rounds, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(dk.hex(), want)
    except (ValueError, TypeError):
        return False


def _readable_password() -> str:
    words = ["pothole", "kerb", "ward", "asphalt", "monsoon", "flyover", "junction", "signal", "lane", "metro"]
    return f"{secrets.choice(words)}-{secrets.choice(words)}-{secrets.randbelow(900) + 100}"


# ---------------------------------------------------------------- setup
def init() -> None:
    with db.connect() as c:
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        demo = [("admin", "Nagrik Roads Admin", "admin"), ("officer", "Ward Officer (demo)", "officer"),
                ("crew", "Repair Crew 07 (demo)", "crew")]
        lines = ["Nagrik Roads demo accounts (created on first start; change these before real use)", ""]
        for username, name, role in demo:
            pw = _readable_password()
            c.execute("INSERT INTO users (username, name, role, password_hash, created_at) VALUES (?,?,?,?,?)",
                      (username, name, role, hash_password(pw), _iso(_now())))
            lines.append(f"{role:8s} username: {username:8s} password: {pw}")
    (db.DB_PATH.parent / "demo_accounts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- sessions
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with db.connect() as c:
        r = c.execute("SELECT u.id, u.username, u.name, u.role, u.active, s.expires_at FROM sessions s "
                      "JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (_token_hash(token),)).fetchone()
    if not r or not r["active"] or r["expires_at"] < _iso(_now()):
        return None
    return {"id": r["id"], "username": r["username"], "name": r["name"], "role": r["role"],
            "role_label": ROLE_LABEL.get(r["role"], r["role"])}


def _rate_limited(request: Request) -> bool:
    ip = request.client.host if request.client else "?"
    for method, rx, n, per in LIMITS:
        if request.method == method and rx.match(request.url.path):
            q = _hits[(ip, rx.pattern)]
            now = time.time()
            while q and now - q[0] > per:
                q.popleft()
            if len(q) >= n:
                return True
            q.append(now)
    return False


async def guard(request: Request, call_next):
    """Middleware: attach the signed-in user and enforce RULES and LIMITS."""
    request.state.user = user_from_request(request) if request.url.path.startswith("/api/") else None
    if _rate_limited(request):
        return JSONResponse({"detail": "Too many requests from this device. Please wait a few minutes and try again."},
                            status_code=429)
    for method, rx, roles in RULES:
        if request.method == method and rx.match(request.url.path):
            u = request.state.user
            if u is None:
                return JSONResponse({"detail": "Sign in as a city official to do this.", "login": True}, status_code=401)
            if u["role"] not in roles:
                return JSONResponse({"detail": f"Your role ({ROLE_LABEL.get(u['role'], u['role'])}) cannot do this."},
                                    status_code=403)
            break
    return await call_next(request)


# ---------------------------------------------------------------- endpoints
class Login(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: Login, response: Response):
    with db.connect() as c:
        r = c.execute("SELECT * FROM users WHERE username=? AND active=1", (body.username.strip().lower(),)).fetchone()
        if not r or not check_password(body.password, r["password_hash"]):
            time.sleep(0.4)  # slow down guessing
            raise HTTPException(401, "Wrong username or password.")
        token = secrets.token_urlsafe(32)
        now = _now()
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                  (_token_hash(token), r["id"], _iso(now), _iso(now + timedelta(hours=SESSION_HOURS))))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (_iso(now), r["id"]))
    response.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", path="/")
    return {"user": {"username": r["username"], "name": r["name"], "role": r["role"],
                     "role_label": ROLE_LABEL.get(r["role"], r["role"])}}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"user": getattr(request.state, "user", None)}


class NewUser(BaseModel):
    username: str
    name: str
    role: str
    password: str


@router.get("/users")
def list_users():
    with db.connect() as c:
        rows = c.execute("SELECT id, username, name, role, active, created_at, last_login FROM users ORDER BY id").fetchall()
    return [{**dict(r), "role_label": ROLE_LABEL.get(r["role"], r["role"])} for r in rows]


@router.post("/users")
def create_user(body: NewUser):
    username = body.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username):
        raise HTTPException(400, "Username: 3 to 32 letters, numbers, dots, dashes or underscores.")
    if body.role not in ROLES:
        raise HTTPException(400, "Role must be admin, officer or crew.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    with db.connect() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise HTTPException(400, "That username is taken.")
        cur = c.execute("INSERT INTO users (username, name, role, password_hash, created_at) VALUES (?,?,?,?,?)",
                        (username, body.name.strip() or username, body.role, hash_password(body.password), _iso(_now())))
    return {"id": cur.lastrowid, "username": username}


class UserUpdate(BaseModel):
    active: bool | None = None
    role: str | None = None
    password: str | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request):
    me_ = request.state.user
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "User not found.")
        if body.active is False and me_ and me_["id"] == user_id:
            raise HTTPException(400, "You cannot disable your own account.")
        if body.role is not None:
            if body.role not in ROLES:
                raise HTTPException(400, "Role must be admin, officer or crew.")
            c.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
        if body.active is not None:
            c.execute("UPDATE users SET active=? WHERE id=?", (1 if body.active else 0, user_id))
            if not body.active:
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        if body.password:
            if len(body.password) < 8:
                raise HTTPException(400, "Password must be at least 8 characters.")
            c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.password), user_id))
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return {"ok": True}
