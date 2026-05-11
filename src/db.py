"""SQLite layer for offer tracking.

States: new | todo | sent | rejected | ignored
"""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


STATES = ("new", "todo", "sent", "rejected", "ignored")


SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL UNIQUE,
    source       TEXT NOT NULL,
    external_id  TEXT,
    title        TEXT NOT NULL,
    company      TEXT,
    location     TEXT,
    contract     TEXT,
    url          TEXT NOT NULL,
    description  TEXT,
    posted_at    TEXT,
    discovered_at TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'new',
    state_changed_at TEXT,
    discord_message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_offers_state ON offers(state);
CREATE INDEX IF NOT EXISTS idx_offers_state_changed ON offers(state_changed_at);
CREATE INDEX IF NOT EXISTS idx_offers_msg ON offers(discord_message_id);
"""


@dataclass
class OfferRow:
    id: int
    source: str
    title: str
    company: Optional[str]
    location: Optional[str]
    contract: Optional[str]
    url: str
    state: str


class Database:
    """Thin async wrapper around sqlite3 using a single bg thread executor."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def _run(self, fn, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._sync_run, fn, *args, **kwargs)

    def _sync_run(self, fn, *args, **kwargs):
        with contextlib.closing(self._connect()) as conn:
            return fn(conn, *args, **kwargs)

    async def init(self) -> None:
        await self._run(lambda conn: conn.executescript(SCHEMA))

    @staticmethod
    def _insert(conn, offer: dict) -> Optional[int]:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO offers
              (fingerprint, source, external_id, title, company, location,
               contract, url, description, posted_at, discovered_at, state,
               state_changed_at)
            VALUES (:fingerprint, :source, :external_id, :title, :company,
                    :location, :contract, :url, :description, :posted_at,
                    :discovered_at, 'new', :discovered_at)
            """,
            offer,
        )
        return cur.lastrowid if cur.rowcount else None

    async def insert_offer(self, offer: dict) -> Optional[int]:
        offer = {**offer, "discovered_at": datetime.now(timezone.utc).isoformat()}
        return await self._run(self._insert, offer)

    async def insert_many(self, offers: Iterable[dict]) -> list[int]:
        now = datetime.now(timezone.utc).isoformat()

        def _do(conn):
            ids = []
            for o in offers:
                o = {**o, "discovered_at": now}
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO offers
                      (fingerprint, source, external_id, title, company,
                       location, contract, url, description, posted_at,
                       discovered_at, state, state_changed_at)
                    VALUES (:fingerprint, :source, :external_id, :title,
                            :company, :location, :contract, :url,
                            :description, :posted_at, :discovered_at,
                            'new', :discovered_at)
                    """,
                    o,
                )
                if cur.rowcount:
                    ids.append(cur.lastrowid)
            return ids

        return await self._run(_do)

    async def get(self, offer_id: int) -> Optional[sqlite3.Row]:
        return await self._run(
            lambda conn: conn.execute(
                "SELECT * FROM offers WHERE id = ?", (offer_id,)
            ).fetchone()
        )

    async def get_by_message(self, message_id: int) -> Optional[sqlite3.Row]:
        return await self._run(
            lambda conn: conn.execute(
                "SELECT * FROM offers WHERE discord_message_id = ?",
                (str(message_id),),
            ).fetchone()
        )

    async def set_message_id(self, offer_id: int, message_id: int) -> None:
        await self._run(
            lambda conn: conn.execute(
                "UPDATE offers SET discord_message_id = ? WHERE id = ?",
                (str(message_id), offer_id),
            )
        )

    async def set_state(self, offer_id: int, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"invalid state: {state}")
        now = datetime.now(timezone.utc).isoformat()
        await self._run(
            lambda conn: conn.execute(
                "UPDATE offers SET state = ?, state_changed_at = ? WHERE id = ?",
                (state, now, offer_id),
            )
        )

    async def list_by_state(self, state: str, limit: int = 50) -> list[sqlite3.Row]:
        return await self._run(
            lambda conn: conn.execute(
                "SELECT * FROM offers WHERE state = ? "
                "ORDER BY discovered_at DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        )

    async def stats(self) -> dict[str, int]:
        def _do(conn):
            rows = conn.execute(
                "SELECT state, COUNT(*) as n FROM offers GROUP BY state"
            ).fetchall()
            out = {s: 0 for s in STATES}
            for r in rows:
                out[r["state"]] = r["n"]
            return out

        return await self._run(_do)

    async def stats_today(self) -> dict[str, int]:
        today = datetime.now(timezone.utc).date().isoformat()

        def _do(conn):
            rows = conn.execute(
                "SELECT state, COUNT(*) as n FROM offers "
                "WHERE date(state_changed_at) = date(?) GROUP BY state",
                (today,),
            ).fetchall()
            out = {s: 0 for s in STATES}
            for r in rows:
                out[r["state"]] = r["n"]
            return out

        return await self._run(_do)

    async def stats_by_source(self) -> dict[str, dict[str, int]]:
        def _do(conn):
            rows = conn.execute(
                "SELECT source, state, COUNT(*) as n FROM offers "
                "GROUP BY source, state"
            ).fetchall()
            out: dict[str, dict[str, int]] = {}
            for r in rows:
                out.setdefault(r["source"], {s: 0 for s in STATES})
                out[r["source"]][r["state"]] = r["n"]
            return out

        return await self._run(_do)

    async def sent_per_day(self, days: int = 7) -> list[tuple[str, int]]:
        """Return [(YYYY-MM-DD, count_sent)] for the last `days` days, oldest first."""
        def _do(conn):
            rows = conn.execute(
                "SELECT date(state_changed_at) as d, COUNT(*) as n "
                "FROM offers "
                "WHERE state = 'sent' "
                "AND date(state_changed_at) >= date('now', ?) "
                "GROUP BY d ORDER BY d",
                (f"-{days - 1} days",),
            ).fetchall()
            return [(r["d"], r["n"]) for r in rows]

        return await self._run(_do)

    async def all_offers(self) -> list[sqlite3.Row]:
        return await self._run(
            lambda conn: conn.execute(
                "SELECT * FROM offers ORDER BY discovered_at DESC"
            ).fetchall()
        )

    async def search(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        like = f"%{query}%"
        return await self._run(
            lambda conn: conn.execute(
                "SELECT * FROM offers WHERE title LIKE ? OR company LIKE ? "
                "OR description LIKE ? ORDER BY discovered_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        )
