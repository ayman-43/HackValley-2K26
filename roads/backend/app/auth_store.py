"""Where accounts and sign-in sessions are stored: MongoDB when ROADSENSE_MONGO_URI is set, otherwise SQLite.

Both stores expose the same small interface, so auth.py does not care which one is in use.
User ids are integers in both (MongoDB uses a counter document), so URLs like /api/auth/users/3 keep working.

A user is a dict: id, username, name, email, google_sub, picture, role, password_hash, active,
created_at, last_login. password_hash is "" for accounts that only sign in with Google.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from . import db

log = logging.getLogger("roadsense.auth")

USER_FIELDS = ("id", "username", "name", "email", "google_sub", "picture", "role", "password_hash",
               "active", "created_at", "last_login", "requested_role", "details")


# ---------------------------------------------------------------- SQLite
class SqliteStore:
    kind = "sqlite"

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

    def init(self) -> None:
        with db.connect() as c:
            c.executescript(self.SCHEMA)
            have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
            for col in ("email", "google_sub", "picture", "requested_role", "details"):  # added later
                if col not in have:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email) WHERE email IS NOT NULL")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_gsub ON users(google_sub) WHERE google_sub IS NOT NULL")

    @staticmethod
    def _user(r) -> dict | None:
        return {k: r[k] for k in USER_FIELDS} if r else None

    def count_users(self) -> int:
        with db.connect() as c:
            return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_user(self, user_id: int) -> dict | None:
        with db.connect() as c:
            return self._user(c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def find_user(self, field: str, value: str) -> dict | None:
        assert field in ("username", "email", "google_sub")
        with db.connect() as c:
            return self._user(c.execute(f"SELECT * FROM users WHERE {field}=?", (value,)).fetchone())

    def list_users(self) -> list[dict]:
        with db.connect() as c:
            return [self._user(r) for r in c.execute("SELECT * FROM users ORDER BY id")]

    def insert_user(self, u: dict) -> int:
        cols = [k for k in USER_FIELDS if k != "id" and k in u]
        with db.connect() as c:
            cur = c.execute(f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [u[k] for k in cols])
            return cur.lastrowid

    def update_user(self, user_id: int, fields: dict) -> None:
        if not fields:
            return
        with db.connect() as c:
            c.execute(f"UPDATE users SET {','.join(k + '=?' for k in fields)} WHERE id=?", [*fields.values(), user_id])

    def create_session(self, token_hash: str, user_id: int, created_at: str, expires_at: str) -> None:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (created_at,))
            c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                      (token_hash, user_id, created_at, expires_at))

    def get_session(self, token_hash: str) -> tuple[dict, str] | None:
        with db.connect() as c:
            s = c.execute("SELECT user_id, expires_at FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
        if not s:
            return None
        u = self.get_user(s["user_id"])
        return (u, s["expires_at"]) if u else None

    def delete_session(self, token_hash: str) -> None:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_user_sessions(self, user_id: int) -> None:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ---------------------------------------------------------------- MongoDB
class MongoStore:
    kind = "mongodb"

    def __init__(self, uri: str, db_name: str | None):
        from pymongo import MongoClient
        self.client = MongoClient(uri, serverSelectionTimeoutMS=8000, appname="nagrik-roads")
        self.client.admin.command("ping")  # fail fast if the URI or network is wrong
        try:
            default = self.client.get_default_database().name
        except Exception:  # URI without a database name
            default = None
        self.db = self.client[db_name or default or "nagrik_roads"]
        self.users = self.db["users"]
        self.sessions = self.db["sessions"]
        self.counters = self.db["counters"]

    def init(self) -> None:
        from pymongo import ASCENDING
        self.users.create_index([("id", ASCENDING)], unique=True)
        self.users.create_index([("username", ASCENDING)], unique=True)
        self.users.create_index([("email", ASCENDING)], unique=True,
                                partialFilterExpression={"email": {"$type": "string"}})
        self.users.create_index([("google_sub", ASCENDING)], unique=True,
                                partialFilterExpression={"google_sub": {"$type": "string"}})
        self.sessions.create_index([("token_hash", ASCENDING)], unique=True)
        # MongoDB deletes expired sessions by itself
        self.sessions.create_index([("expires_dt", ASCENDING)], expireAfterSeconds=0)

    @staticmethod
    def _user(d) -> dict | None:
        return {k: d.get(k) for k in USER_FIELDS} if d else None

    def count_users(self) -> int:
        return self.users.count_documents({})

    def get_user(self, user_id: int) -> dict | None:
        return self._user(self.users.find_one({"id": int(user_id)}))

    def find_user(self, field: str, value: str) -> dict | None:
        assert field in ("username", "email", "google_sub")
        return self._user(self.users.find_one({field: value}))

    def list_users(self) -> list[dict]:
        return [self._user(d) for d in self.users.find({}).sort("id", 1)]

    def _next_id(self) -> int:
        from pymongo import ReturnDocument
        top = self.users.find_one({}, sort=[("id", -1)])
        floor = top["id"] if top else 0
        self.counters.update_one({"_id": "users"}, {"$max": {"seq": floor}}, upsert=True)
        doc = self.counters.find_one_and_update({"_id": "users"}, {"$inc": {"seq": 1}},
                                                return_document=ReturnDocument.AFTER)
        return int(doc["seq"])

    def insert_user(self, u: dict) -> int:
        doc = {k: u.get(k) for k in USER_FIELDS if k != "id"}
        doc["id"] = int(u["id"]) if u.get("id") else self._next_id()
        doc["active"] = int(doc.get("active") if doc.get("active") is not None else 1)
        self.users.insert_one(doc)
        return doc["id"]

    def update_user(self, user_id: int, fields: dict) -> None:
        if fields:
            self.users.update_one({"id": int(user_id)}, {"$set": fields})

    def create_session(self, token_hash: str, user_id: int, created_at: str, expires_at: str) -> None:
        self.sessions.insert_one({"token_hash": token_hash, "user_id": int(user_id), "created_at": created_at,
                                  "expires_at": expires_at, "expires_dt": datetime.fromisoformat(expires_at)})

    def get_session(self, token_hash: str) -> tuple[dict, str] | None:
        s = self.sessions.find_one({"token_hash": token_hash})
        if not s:
            return None
        u = self.get_user(s["user_id"])
        return (u, s["expires_at"]) if u else None

    def delete_session(self, token_hash: str) -> None:
        self.sessions.delete_one({"token_hash": token_hash})

    def delete_user_sessions(self, user_id: int) -> None:
        self.sessions.delete_many({"user_id": int(user_id)})


# ---------------------------------------------------------------- choose
def open_store():
    """MongoDB if ROADSENSE_MONGO_URI is set and reachable, else SQLite. Returns (store, problem_or_None)."""
    uri = os.environ.get("ROADSENSE_MONGO_URI", "").strip()
    if uri:
        try:
            store = MongoStore(uri, os.environ.get("ROADSENSE_MONGO_DB", "").strip() or None)
            store.init()
            log.warning("accounts: using MongoDB database '%s'", store.db.name)
            return store, None
        except Exception as e:  # keep the app usable during a demo even if Atlas is unreachable
            msg = f"MongoDB not reachable ({type(e).__name__}: {str(e)[:200]}); using the local SQLite accounts instead"
            log.error("accounts: %s", msg)
            print(f"[auth] WARNING: {msg}", flush=True)
            problem = msg
    else:
        problem = None
    store = SqliteStore()
    store.init()
    return store, problem
