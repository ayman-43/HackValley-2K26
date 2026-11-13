"""Sign-in: accounts with roles, cookie sessions, Google sign-in, and server-side access rules.

Who can do what (enforced here for every request, so the browser cannot bypass it):
  * everyone (no login): read everything (map, defects, reports), report a pothole
    (POST /api/inspections) and run a live scan; both are rate-limited per IP against spam
  * citizen (anyone who signs in with Google): the same as the public, but signed in by name.
    An administrator can promote a citizen to officer or crew on the accounts page.
  * officer + admin: review detections, change workflow, auto-approve, export training data
  * crew + officer + admin: mark a defect repaired / repaired-by, plan the repair route
  * admin: manage users

Accounts and sessions live in MongoDB when ROADSENSE_MONGO_URI is set, otherwise in SQLite
(see auth_store.py). Passwords are PBKDF2-SHA256 hashes with a per-user salt. A session is a random token
in an HttpOnly cookie; only its SHA-256 is stored.

Google sign-in (ROADSENSE_GOOGLE_CLIENT_ID): the browser gets a Google ID token from Google's button and
posts it to /api/auth/google; the server checks its signature, audience and verified email. An official
whose account has the same email signs in as that official; ROADSENSE_ADMIN_EMAILS (comma separated) become
administrators on first Google sign-in; everyone else becomes a citizen.

On first start, if there are no users, three demo accounts are created (or, for a new MongoDB database, the
existing SQLite accounts are copied over). Demo passwords go to backend/data/demo_accounts.txt (git-ignored).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db
from .auth_store import SqliteStore, open_store

log = logging.getLogger("roadsense.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "rs_session"
SESSION_HOURS = 12
ROLES = ("admin", "officer", "crew", "citizen")
ROLE_LABEL = {"admin": "Administrator", "officer": "Ward officer", "crew": "Repair crew", "citizen": "Citizen"}
PBKDF2_ROUNDS = 200_000
GOOGLE_CLIENT_ID = os.environ.get("ROADSENSE_GOOGLE_CLIENT_ID", "").strip()
SUPABASE_URL = os.environ.get("ROADSENSE_SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.environ.get("ROADSENSE_SUPABASE_KEY", "").strip()  # publishable (public) key only
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ROADSENSE_ADMIN_EMAILS", "").split(",") if e.strip()}

STORE = None          # set by init()
STORE_PROBLEM = None  # why MongoDB is not used although configured

# (method, path regex) -> roles allowed. Anything not listed here is public.
RULES = [
    ("POST", r"^/api/observations/\d+/review$", {"officer", "admin"}),
    ("POST", r"^/api/review/auto_approve$", {"officer", "admin"}),
    ("PATCH", r"^/api/defects/\d+/workflow$", {"officer", "admin"}),
    ("GET", r"^/api/export/corrections$", {"officer", "admin"}),
    ("GET", r"^/api/review/queue$", {"officer", "admin"}),
    ("GET", r"^/api/digest$", {"officer", "admin"}),
    ("GET", r"^/api/report/", {"officer", "admin"}),
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
    ("POST", re.compile(r"^/api/auth/login$"), 20, 300),
    ("POST", re.compile(r"^/api/auth/google$"), 20, 300),
    ("POST", re.compile(r"^/api/auth/supabase$"), 20, 300),
    ("POST", re.compile(r"^/api/auth/signup$"), 10, 3600),
]
_hits: dict[tuple, deque] = defaultdict(deque)

# Session lookups happen on every /api/ request; with a remote MongoDB that is a network round trip,
# so results are kept for a few seconds. Any sign-out or account change clears the cache.
_CACHE_SECONDS = 20
_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()


def _forget_sessions() -> None:
    with _cache_lock:
        _cache.clear()


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
    except (ValueError, TypeError, AttributeError):
        return False


def _readable_password() -> str:
    words = ["pothole", "kerb", "ward", "asphalt", "monsoon", "flyover", "junction", "signal", "lane", "metro"]
    return f"{secrets.choice(words)}-{secrets.choice(words)}-{secrets.randbelow(900) + 100}"


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"],
            "role_label": ROLE_LABEL.get(u["role"], u["role"]), "email": u.get("email"),
            "picture": u.get("picture"), "google": bool(u.get("google_sub")),
            "requested_role": u.get("requested_role") or None,
            "requested_role_label": ROLE_LABEL.get(u.get("requested_role") or "", None)}


# ---------------------------------------------------------------- setup
def init() -> None:
    global STORE, STORE_PROBLEM
    STORE, STORE_PROBLEM = open_store()
    if STORE.count_users():
        return
    if STORE.kind == "mongodb":
        # new MongoDB database: bring over the accounts that already exist locally (same passwords)
        local = SqliteStore()
        local.init()
        old = local.list_users()
        if old:
            for u in old:
                STORE.insert_user(u)
            print(f"[auth] copied {len(old)} existing accounts from SQLite into MongoDB", flush=True)
            return
    demo = [("admin", "Nagrik Roads Admin", "admin"), ("officer", "Ward Officer (demo)", "officer"),
            ("crew", "Repair Crew 07 (demo)", "crew")]
    lines = ["Nagrik Roads demo accounts (created on first start; change these before real use)", ""]
    for username, name, role in demo:
        pw = _readable_password()
        STORE.insert_user({"username": username, "name": name, "role": role, "password_hash": hash_password(pw),
                           "active": 1, "created_at": _iso(_now())})
        lines.append(f"{role:8s} username: {username:8s} password: {pw}")
    (db.DB_PATH.parent / "demo_accounts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- sessions
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    th = _token_hash(token)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(th)
    if hit and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    try:
        found = STORE.get_session(th)
    except Exception as e:  # database hiccup: treat as signed out rather than crash every request
        log.error("session lookup failed: %s", e)
        return None
    user = None
    if found:
        u, expires_at = found
        if u.get("active") and expires_at >= _iso(_now()):
            user = public_user(u)
    with _cache_lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[th] = (now, user)
    return user


def _start_session(response: Response, user: dict) -> None:
    token = secrets.token_urlsafe(32)
    now = _now()
    STORE.create_session(_token_hash(token), user["id"], _iso(now), _iso(now + timedelta(hours=SESSION_HOURS)))
    STORE.update_user(user["id"], {"last_login": _iso(now)})
    response.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", path="/")


def client_ip(request: Request) -> str:
    """The visitor's address. Behind the local Nagrik site (Node on :5501) every request comes from 127.0.0.1,
    so the X-Forwarded-For it adds is used, but only when the request itself comes from this machine."""
    ip = request.client.host if request.client else "?"
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd and ip in ("127.0.0.1", "::1", "localhost"):
        ip = fwd.split(",")[0].strip().removeprefix("::ffff:") or ip
    return ip


def _rate_limited(request: Request) -> bool:
    ip = client_ip(request)
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
    request.state.user = (await run_in_threadpool(user_from_request, request)
                          if request.url.path.startswith("/api/") else None)
    if _rate_limited(request):
        return JSONResponse({"detail": "Too many requests from this device. Please wait a few minutes and try again."},
                            status_code=429)
    for method, rx, roles in RULES:
        if request.method == method and rx.match(request.url.path):
            u = request.state.user
            if u is None:
                return JSONResponse({"detail": "Sign in as a city official to do this.", "login": True}, status_code=401)
            if u["role"] not in roles:
                return JSONResponse({"detail": f"Your role ({ROLE_LABEL.get(u['role'], u['role'])}) cannot do this. "
                                               "Ask an administrator for official access."}, status_code=403)
            break
    return await call_next(request)


# ---------------------------------------------------------------- endpoints
@router.get("/config")
def config():
    """What the sign-in page should offer."""
    sb = bool(SUPABASE_URL and SUPABASE_KEY)
    return {"google_client_id": GOOGLE_CLIENT_ID or None, "store": STORE.kind if STORE else None,
            "supabase_url": SUPABASE_URL if sb else None, "supabase_key": SUPABASE_KEY if sb else None,
            "store_problem": STORE_PROBLEM}


class Login(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: Login, response: Response):
    ident = body.username.strip().lower()
    u = STORE.find_user("email" if "@" in ident else "username", ident)
    stored = (u or {}).get("password_hash") or ""
    # a password pasted with a stray space at either end still works
    ok = check_password(body.password, stored) or (body.password != body.password.strip()
                                                    and check_password(body.password.strip(), stored))
    if not u or not u.get("active") or not ok:
        time.sleep(0.4)  # slow down guessing
        raise HTTPException(401, "Wrong username or password.")
    _start_session(response, u)
    return {"user": public_user(u)}


class Signup(BaseModel):
    name: str
    email: str
    password: str
    role: str = "citizen"        # citizen | officer | crew (officer/crew need an administrator's approval)
    employee_id: str | None = None
    ward: str | None = None


@router.post("/signup")
def signup(body: Signup, response: Response):
    """Public sign-up creates a CITIZEN account and signs it in. Official accounts (ward officer, repair crew,
    administrator) are never self-service: an administrator creates them on the accounts page. Any role sent
    by the client is ignored (older clients may still send one)."""
    name = body.name.strip()
    email = _clean_email(body.email)
    if not name or len(name) > 80:
        raise HTTPException(400, "Enter your name.")
    if not email:
        raise HTTPException(400, "Enter your email address.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if STORE.find_user("email", email):
        raise HTTPException(400, "An account with this email already exists. Sign in instead.")
    role = "admin" if email in ADMIN_EMAILS else "citizen"
    uid = STORE.insert_user({"username": _unique_username(email), "name": name, "email": email, "role": role,
                             "requested_role": None, "details": None,
                             "password_hash": hash_password(body.password), "active": 1,
                             "created_at": _iso(_now())})
    u = STORE.get_user(uid)
    _start_session(response, u)
    return {"user": public_user(u)}


class GoogleLogin(BaseModel):
    credential: str


def _verify_google(credential: str) -> dict:
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token
    try:
        info = id_token.verify_oauth2_token(credential, g_requests.Request(), GOOGLE_CLIENT_ID, clock_skew_in_seconds=10)
    except ValueError as e:
        raise HTTPException(401, f"Google sign-in could not be verified ({e}).") from e
    except Exception as e:  # network problem fetching Google's keys
        raise HTTPException(503, f"Could not reach Google to check the sign-in ({type(e).__name__}). Try again.") from e
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(401, "That sign-in did not come from Google.")
    if not info.get("email") or not info.get("email_verified"):
        raise HTTPException(401, "Your Google account email is not verified.")
    return info


def _unique_username(email: str) -> str:
    base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower())[:24] or "user"
    if len(base) < 3:
        base = (base + "user")[:6]
    name, n = base, 1
    while STORE.find_user("username", name):
        n += 1
        name = f"{base}{n}"
    return name


@router.post("/google")
def google_login(body: GoogleLogin, response: Response):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Google sign-in is not set up on this server (ROADSENSE_GOOGLE_CLIENT_ID).")
    info = _verify_google(body.credential)
    email = info["email"].strip().lower()
    return _external_sign_in(response, info["sub"], email, (info.get("name") or email.split("@")[0]).strip(),
                             info.get("picture"))


class SupabaseLogin(BaseModel):
    access_token: str


@router.post("/supabase")
def supabase_login(body: SupabaseLogin, response: Response):
    """Sign-in done by Supabase Auth (Google etc.) in the browser: ask Supabase who the token belongs to."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        raise HTTPException(400, "Supabase sign-in is not set up on this server (ROADSENSE_SUPABASE_URL / _KEY).")
    import httpx
    try:
        r = httpx.get(f"{SUPABASE_URL}/auth/v1/user", timeout=15,
                      headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {body.access_token}"})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"Could not reach Supabase to check the sign-in ({type(e).__name__}). Try again.") from e
    if r.status_code != 200:
        raise HTTPException(401, "Supabase did not accept this sign-in. Please sign in again.")
    info = r.json()
    email = (info.get("email") or "").strip().lower()
    if not email or not (info.get("email_confirmed_at") or info.get("confirmed_at")):
        raise HTTPException(401, "Your email is not verified.")
    meta = info.get("user_metadata") or {}
    name = (meta.get("full_name") or meta.get("name") or email.split("@")[0]).strip()
    return _external_sign_in(response, "supabase:" + info["id"], email, name,
                             meta.get("avatar_url") or meta.get("picture"))


def _external_sign_in(response: Response, sub: str, email: str, name: str, picture: str | None):
    """Google or Supabase sign-in: match an existing account by id or email, else create a citizen."""
    u = STORE.find_user("google_sub", sub) or STORE.find_user("email", email)
    if u:
        if not u.get("active"):
            raise HTTPException(403, "This account is disabled. Ask an administrator.")
        fields = {"google_sub": sub, "email": email, "picture": picture}
        if email in ADMIN_EMAILS and u["role"] != "admin":
            fields["role"] = "admin"
        STORE.update_user(u["id"], fields)
        u = STORE.get_user(u["id"])
    else:
        uid = STORE.insert_user({"username": _unique_username(email), "name": name, "email": email,
                                 "google_sub": sub, "picture": picture,
                                 "role": "admin" if email in ADMIN_EMAILS else "citizen",
                                 "password_hash": "", "active": 1, "created_at": _iso(_now())})
        u = STORE.get_user(uid)
    _start_session(response, u)
    return {"user": public_user(u)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        STORE.delete_session(_token_hash(token))
        _forget_sessions()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"user": getattr(request.state, "user", None)}


class NewUser(BaseModel):
    username: str
    name: str
    role: str
    password: str = ""
    email: str | None = None


def _clean_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "That email address does not look right.")
    return email


@router.get("/users")
def list_users():
    out = []
    for u in STORE.list_users():
        out.append({**public_user(u), "active": u.get("active"), "created_at": u.get("created_at"),
                    "details": u.get("details"),
                    "last_login": u.get("last_login"), "has_password": bool(u.get("password_hash"))})
    return out


@router.post("/users")
def create_user(body: NewUser):
    username = body.username.strip().lower()
    email = _clean_email(body.email)
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username):
        raise HTTPException(400, "Username: 3 to 32 letters, numbers, dots, dashes or underscores.")
    if body.role not in ROLES:
        raise HTTPException(400, "Role must be admin, officer, crew or citizen.")
    if not email and len(body.password) < 8:
        raise HTTPException(400, "Give a password of at least 8 characters, or a Google email to sign in with.")
    if body.password and len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if STORE.find_user("username", username):
        raise HTTPException(400, "That username is taken.")
    if email and STORE.find_user("email", email):
        raise HTTPException(400, "An account with that email already exists. Change its role instead.")
    uid = STORE.insert_user({"username": username, "name": body.name.strip() or username, "email": email,
                             "role": body.role, "password_hash": hash_password(body.password) if body.password else "",
                             "active": 1, "created_at": _iso(_now())})
    return {"id": uid, "username": username}


class UserUpdate(BaseModel):
    active: bool | None = None
    role: str | None = None
    password: str | None = None
    email: str | None = None
    decline_request: bool | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request):
    me_ = request.state.user
    if not STORE.get_user(user_id):
        raise HTTPException(404, "User not found.")
    if body.active is False and me_ and me_["id"] == user_id:
        raise HTTPException(400, "You cannot disable your own account.")
    fields = {}
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, "Role must be admin, officer, crew or citizen.")
        if me_ and me_["id"] == user_id and body.role != "admin":
            raise HTTPException(400, "You cannot remove your own administrator role.")
        fields["role"] = body.role
        fields["requested_role"] = None  # a role decision answers any pending request
    if body.decline_request:
        fields["requested_role"] = None
    if body.active is not None:
        fields["active"] = 1 if body.active else 0
    if body.email is not None:
        email = _clean_email(body.email)
        other = STORE.find_user("email", email) if email else None
        if other and other["id"] != user_id:
            raise HTTPException(400, "Another account already uses that email.")
        fields["email"] = email
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters.")
        fields["password_hash"] = hash_password(body.password)
    STORE.update_user(user_id, fields)
    if body.active is False or body.password:
        STORE.delete_user_sessions(user_id)
    _forget_sessions()
    return {"ok": True}
