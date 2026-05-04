"""SQLite data access layer for the chores add-on.

Schema overview:
    users            - family members (kids and adults)
    chores           - chore definitions with effort/reward/frequency
    chore_users      - many-to-many for "specific" assignment chores
    completions      - log of completed chores per user per day
    payouts          - record of allowance paid (idempotent per day per user)

A chore is "due" on a given date based on its frequency definition. A chore
can be either OPEN (anyone can complete it once per due-date) or SPECIFIC
(linked to one or more users via chore_users; each linked user must complete
their own copy).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Iterable, Iterator, Optional

DB_PATH = os.environ.get("DB_PATH", "/data/chores.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_kid INTEGER NOT NULL DEFAULT 0,
    color TEXT DEFAULT '#7c9cff',
    allowance_amount REAL NOT NULL DEFAULT 0,
    allowance_threshold INTEGER NOT NULL DEFAULT 10,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    effort INTEGER NOT NULL DEFAULT 1,
    reward_amount REAL NOT NULL DEFAULT 0,
    -- frequency: 'daily', 'weekly', 'monthly', 'interval'
    frequency_type TEXT NOT NULL DEFAULT 'daily',
    -- frequency_data interpretation:
    --   daily      -> ignored
    --   weekly     -> CSV of weekday numbers (0=Mon ... 6=Sun), e.g. "0,2,4"
    --   monthly    -> day of month, e.g. "1" or "15"
    --   interval   -> "every N days", e.g. "3"
    frequency_data TEXT DEFAULT '',
    -- assignment: 'open' (anyone) or 'specific' (linked users only)
    assignment_type TEXT NOT NULL DEFAULT 'open',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chore_users (
    chore_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (chore_id, user_id),
    FOREIGN KEY (chore_id) REFERENCES chores(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chore_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    due_date TEXT NOT NULL,           -- YYYY-MM-DD the chore was due
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effort_earned INTEGER NOT NULL,
    reward_earned REAL NOT NULL DEFAULT 0,
    UNIQUE(chore_id, user_id, due_date),
    FOREIGN KEY (chore_id) REFERENCES chores(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    payout_date TEXT NOT NULL,
    amount REAL NOT NULL,
    effort_total INTEGER NOT NULL,
    UNIQUE(user_id, payout_date),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_completions_due ON completions(due_date);
CREATE INDEX IF NOT EXISTS idx_completions_user_due ON completions(user_id, due_date);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create the database schema if it doesn't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------- Users ----------

def list_users() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute("SELECT * FROM users ORDER BY is_kid DESC, name"))


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(
    name: str,
    is_kid: bool,
    color: str,
    allowance_amount: float,
    allowance_threshold: int,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (name, is_kid, color, allowance_amount, allowance_threshold)
               VALUES (?, ?, ?, ?, ?)""",
            (name, int(is_kid), color, allowance_amount, allowance_threshold),
        )
        return cur.lastrowid


def update_user(
    user_id: int,
    name: str,
    is_kid: bool,
    color: str,
    allowance_amount: float,
    allowance_threshold: int,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE users SET name=?, is_kid=?, color=?, allowance_amount=?,
                                  allowance_threshold=? WHERE id=?""",
            (name, int(is_kid), color, allowance_amount, allowance_threshold, user_id),
        )


def delete_user(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------- Chores ----------

def list_chores() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM chores ORDER BY name").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["assigned_user_ids"] = [
                u["user_id"]
                for u in conn.execute(
                    "SELECT user_id FROM chore_users WHERE chore_id = ?", (r["id"],)
                )
            ]
            result.append(d)
        return result


def get_chore(chore_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chores WHERE id = ?", (chore_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["assigned_user_ids"] = [
            u["user_id"]
            for u in conn.execute(
                "SELECT user_id FROM chore_users WHERE chore_id = ?", (chore_id,)
            )
        ]
        return d


def upsert_chore(
    *,
    chore_id: Optional[int],
    name: str,
    description: str,
    effort: int,
    reward_amount: float,
    frequency_type: str,
    frequency_data: str,
    assignment_type: str,
    active: bool,
    assigned_user_ids: Iterable[int],
) -> int:
    with get_conn() as conn:
        if chore_id is None:
            cur = conn.execute(
                """INSERT INTO chores
                   (name, description, effort, reward_amount, frequency_type,
                    frequency_data, assignment_type, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description, effort, reward_amount, frequency_type,
                 frequency_data, assignment_type, int(active)),
            )
            chore_id = cur.lastrowid
        else:
            conn.execute(
                """UPDATE chores SET name=?, description=?, effort=?, reward_amount=?,
                                       frequency_type=?, frequency_data=?,
                                       assignment_type=?, active=? WHERE id=?""",
                (name, description, effort, reward_amount, frequency_type,
                 frequency_data, assignment_type, int(active), chore_id),
            )
            conn.execute("DELETE FROM chore_users WHERE chore_id = ?", (chore_id,))

        for uid in assigned_user_ids:
            conn.execute(
                "INSERT OR IGNORE INTO chore_users (chore_id, user_id) VALUES (?, ?)",
                (chore_id, uid),
            )
        return chore_id


def delete_chore(chore_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM chores WHERE id = ?", (chore_id,))


# ---------- Due-date logic ----------

def chore_is_due(chore: dict, on_date: date) -> bool:
    """Return True if `chore` is due on `on_date` per its frequency settings."""
    if not chore["active"]:
        return False
    ftype = chore["frequency_type"]
    fdata = (chore.get("frequency_data") or "").strip()

    if ftype == "daily":
        return True
    if ftype == "weekly":
        if not fdata:
            return False
        try:
            allowed = {int(x) for x in fdata.split(",") if x.strip() != ""}
        except ValueError:
            return False
        return on_date.weekday() in allowed
    if ftype == "monthly":
        try:
            return on_date.day == int(fdata)
        except ValueError:
            return False
    if ftype == "interval":
        try:
            n = int(fdata)
        except ValueError:
            return False
        if n <= 0:
            return False
        # Anchor on chore creation date; interval counted from there.
        try:
            anchor = datetime.fromisoformat(chore["created_at"]).date()
        except ValueError:
            anchor = on_date
        return (on_date - anchor).days % n == 0
    return False


# ---------- Completions ----------

def mark_complete(chore_id: int, user_id: int, due_date: date) -> bool:
    """Record completion. Returns True if newly inserted, False if not.

    For OPEN chores, only the first user to claim per due_date succeeds; later
    attempts return False even if the second user has not personally claimed.
    For SPECIFIC chores, each assigned user has their own claim slot.
    """
    chore = get_chore(chore_id)
    if not chore:
        return False
    if chore["assignment_type"] == "open":
        existing = is_open_chore_complete(chore_id, due_date)
        if existing is not None:
            return False
    elif chore["assignment_type"] == "specific":
        if user_id not in chore["assigned_user_ids"]:
            return False
    with get_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO completions
                   (chore_id, user_id, due_date, effort_earned, reward_earned)
                   VALUES (?, ?, ?, ?, ?)""",
                (chore_id, user_id, due_date.isoformat(),
                 chore["effort"], chore["reward_amount"]),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def unmark_complete(chore_id: int, user_id: int, due_date: date) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM completions WHERE chore_id=? AND user_id=? AND due_date=?",
            (chore_id, user_id, due_date.isoformat()),
        )


def completions_for_date(due_date: date) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute(
            "SELECT * FROM completions WHERE due_date = ?", (due_date.isoformat(),)
        ))


def user_effort_today(user_id: int, on_date: date) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(effort_earned), 0) AS total
               FROM completions WHERE user_id=? AND due_date=?""",
            (user_id, on_date.isoformat()),
        ).fetchone()
        return int(row["total"])


def user_reward_today(user_id: int, on_date: date) -> float:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(reward_earned), 0) AS total
               FROM completions WHERE user_id=? AND due_date=?""",
            (user_id, on_date.isoformat()),
        ).fetchone()
        return float(row["total"])


def is_chore_complete_for_user(chore_id: int, user_id: int, due_date: date) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM completions
               WHERE chore_id=? AND user_id=? AND due_date=?""",
            (chore_id, user_id, due_date.isoformat()),
        ).fetchone()
        return row is not None


def is_open_chore_complete(chore_id: int, due_date: date) -> Optional[int]:
    """For an open chore, return the user_id who completed it (or None)."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT user_id FROM completions
               WHERE chore_id=? AND due_date=? LIMIT 1""",
            (chore_id, due_date.isoformat()),
        ).fetchone()
        return row["user_id"] if row else None


# ---------- Payouts (allowance) ----------

def record_payout(user_id: int, payout_date: date, amount: float, effort_total: int) -> bool:
    with get_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO payouts (user_id, payout_date, amount, effort_total)
                   VALUES (?, ?, ?, ?)""",
                (user_id, payout_date.isoformat(), amount, effort_total),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def has_payout(user_id: int, payout_date: date) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM payouts WHERE user_id=? AND payout_date=?",
            (user_id, payout_date.isoformat()),
        ).fetchone()
        return row is not None


def payout_history(user_id: int, days: int = 30) -> list[sqlite3.Row]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        return list(conn.execute(
            """SELECT * FROM payouts WHERE user_id=? AND payout_date >= ?
               ORDER BY payout_date DESC""",
            (user_id, cutoff),
        ))
