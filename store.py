"""Storage: accounts, login tokens, and counseling sessions.

A counseling session is kept as a JSON blob because its shape is the counselor's
business, not the database's. The columns beside it exist so sessions can be
listed and owner-checked without parsing every blob.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "career.db")


def connect():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL lets a second process (a local dev server alongside the public one)
    # read while the other writes, instead of erroring with "database is locked".
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                   id         TEXT PRIMARY KEY,
                   name       TEXT,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   has_plan   INTEGER NOT NULL DEFAULT 0,
                   data       TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   id         TEXT PRIMARY KEY,
                   email      TEXT NOT NULL UNIQUE,
                   name       TEXT NOT NULL,
                   digest     TEXT NOT NULL,
                   salt       TEXT NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS logins (
                   token      TEXT PRIMARY KEY,
                   user_id    TEXT NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        # Sessions predating accounts have no owner; adopt the column in place.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "user_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_id, updated_at)")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- accounts --------------------------------------------------------------

def create_user(email, name, digest, salt):
    user_id = uuid.uuid4().hex[:12]
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, name, digest, salt, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, email, name, digest, salt, now()),
        )
    return user_id


def user_by_email(email):
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def start_login(user_id, token):
    with connect() as conn:
        conn.execute(
            "INSERT INTO logins (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, now()),
        )


def user_by_token(token):
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT users.* FROM logins
               JOIN users ON users.id = logins.user_id
               WHERE logins.token = ?""",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def end_login(token):
    with connect() as conn:
        conn.execute("DELETE FROM logins WHERE token = ?", (token,))


# ---- counseling sessions ---------------------------------------------------

def new(session_id, user_id):
    session = {
        "id": session_id,
        "user_id": user_id,
        "profile": {"extra": []},
        "history": [],
        "stage": "core",       # core -> followup -> ready -> done
        "queue": [],           # follow-up questions still to ask
        "pending": None,       # the follow-up question awaiting an answer
        "plan": None,
        "created_at": now(),
    }
    save(session)
    return session


def load(session_id):
    with connect() as conn:
        row = conn.execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def save(session):
    with connect() as conn:
        conn.execute(
            """INSERT INTO sessions (id, user_id, name, created_at, updated_at, has_plan, data)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   user_id = excluded.user_id,
                   name = excluded.name,
                   updated_at = excluded.updated_at,
                   has_plan = excluded.has_plan,
                   data = excluded.data""",
            (
                session["id"],
                session.get("user_id"),
                session["profile"].get("name") or "",
                session.get("created_at") or now(),
                now(),
                1 if session.get("plan") else 0,
                json.dumps(session, ensure_ascii=False),
            ),
        )


def sessions_for(user_id, limit=25):
    """A user's own sessions, newest first, with just enough to list them."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, name, created_at, updated_at, has_plan, data FROM sessions
               WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()

    listed = []
    for row in rows:
        blob = json.loads(row["data"])
        plan = blob.get("plan") or {}
        listed.append(
            {
                "id": row["id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "has_plan": bool(row["has_plan"]),
                "headline": plan.get("headline") or "",
                "answered": sum(1 for k, v in blob["profile"].items() if k != "extra" and v),
            }
        )
    return listed
