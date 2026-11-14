import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

STATUSES = ("pending", "approved", "rejected", "expired")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_mbid TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    album_mbid TEXT NOT NULL,
    album_title TEXT NOT NULL,
    year INTEGER,
    message_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    posted_at TEXT NOT NULL,
    answered_at TEXT,
    noted INTEGER NOT NULL DEFAULT 0
)
"""


@dataclass
class Suggestion:
    id: int
    artist_mbid: str
    artist_name: str
    album_mbid: str
    album_title: str
    year: Optional[int]
    message_id: str
    status: str
    posted_at: datetime
    answered_at: Optional[datetime]
    noted: bool


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _row(r) -> Suggestion:
    return Suggestion(
        id=r["id"], artist_mbid=r["artist_mbid"], artist_name=r["artist_name"],
        album_mbid=r["album_mbid"], album_title=r["album_title"], year=r["year"],
        message_id=r["message_id"], status=r["status"],
        posted_at=datetime.fromisoformat(r["posted_at"]),
        answered_at=datetime.fromisoformat(r["answered_at"]) if r["answered_at"] else None,
        noted=bool(r["noted"]),
    )


class Store:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _exec(self, sql, args=()):
        c = self._conn()
        try:
            with c:
                return c.execute(sql, args)
        finally:
            c.close()

    def _query(self, sql, args=()):
        c = self._conn()
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def add_suggestion(self, artist_mbid, artist_name, album_mbid, album_title,
                       year, message_id, now: datetime) -> int:
        cur = self._exec(
            "INSERT INTO suggestions (artist_mbid, artist_name, album_mbid, album_title,"
            " year, message_id, status, posted_at) VALUES (?,?,?,?,?,?,'pending',?)",
            ((artist_mbid or "").lower(), artist_name, (album_mbid or "").lower(), album_title, year,
             str(message_id), _iso(now)))
        return cur.lastrowid

    def get(self, id: int) -> Optional[Suggestion]:
        rows = self._query("SELECT * FROM suggestions WHERE id=?", (id,))
        return _row(rows[0]) if rows else None

    def by_message(self, message_id) -> Optional[Suggestion]:
        rows = self._query("SELECT * FROM suggestions WHERE message_id=?", (str(message_id),))
        return _row(rows[0]) if rows else None

    def set_status(self, id: int, status: str, now: datetime) -> bool:
        """Move a pending row to status. True only if this call changed it."""
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        cur = self._exec(
            "UPDATE suggestions SET status=?, answered_at=? WHERE id=? AND status='pending'",
            (status, _iso(now), id))
        return cur.rowcount == 1

    def claim(self, id: int, now: Optional[datetime] = None) -> bool:
        """Atomically pending -> approved. True only for the winning caller."""
        now = now or datetime.now(timezone.utc)
        cur = self._exec(
            "UPDATE suggestions SET status='approved', answered_at=?"
            " WHERE id=? AND status='pending'", (_iso(now), id))
        return cur.rowcount == 1

    def reopen(self, id: int) -> bool:
        """approved -> pending (revert a failed request)."""
        cur = self._exec(
            "UPDATE suggestions SET status='pending', answered_at=NULL"
            " WHERE id=? AND status='approved'", (id,))
        return cur.rowcount == 1

    def pending_count(self) -> int:
        return self._query("SELECT COUNT(*) AS n FROM suggestions WHERE status='pending'")[0]["n"]

    def pending_ids(self) -> list:
        return [r["id"] for r in self._query(
            "SELECT id FROM suggestions WHERE status='pending' ORDER BY id")]

    def known_artist_mbids(self, now: datetime, cooldown_days: int = 60) -> set:
        cutoff = _iso(now - timedelta(days=cooldown_days))
        rows = self._query(
            "SELECT DISTINCT artist_mbid FROM suggestions"
            " WHERE status IN ('rejected','pending') OR posted_at > ?", (cutoff,))
        return {r["artist_mbid"].lower() for r in rows}

    def approved_without_note(self, now: datetime, days: int = 7) -> list:
        cutoff = _iso(now - timedelta(days=days))
        rows = self._query(
            "SELECT * FROM suggestions WHERE status='approved' AND noted=0"
            " AND answered_at IS NOT NULL AND answered_at <= ? ORDER BY id", (cutoff,))
        return [_row(r) for r in rows]

    def mark_noted(self, id: int) -> None:
        self._exec("UPDATE suggestions SET noted=1 WHERE id=?", (id,))

    def last_post_at(self) -> Optional[datetime]:
        rows = self._query("SELECT posted_at FROM suggestions")
        times = [datetime.fromisoformat(r["posted_at"]) for r in rows]
        return max(times) if times else None
