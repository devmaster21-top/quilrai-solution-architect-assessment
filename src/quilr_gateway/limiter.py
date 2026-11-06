"""Process-safe, on-disk SQLite sliding window with atomic token reservations."""

import asyncio
import hashlib
import math
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class TokenLimiter:
    def __init__(
        self,
        path: str,
        limit: int = 50_000,
        window: float = 60,
        clock: Callable[[], float] = time.time,
    ):
        if path == ":memory:" or limit < 1 or window <= 0:
            raise ValueError("An on-disk database and positive limits are required")
        self.path, self.limit, self.window, self.clock = path, limit, window, clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS reservations "
                "(tenant TEXT NOT NULL, created REAL NOT NULL, tokens INTEGER NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS tenant_window ON reservations(tenant, created)")
            db.execute("CREATE INDEX IF NOT EXISTS expiry ON reservations(created)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    async def reserve(self, tenant: str, tokens: int) -> None:
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
            raise ValueError("tokens must be a positive integer")
        await asyncio.to_thread(self._reserve, hashlib.sha256(tenant.encode()).hexdigest(), tokens)

    def _reserve(self, tenant: str, tokens: int) -> None:
        with self._connect() as db:
            # Serializes check+insert across workers, not just within this event loop.
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            db.execute("DELETE FROM reservations WHERE created <= ?", (now - self.window,))
            rows = db.execute(
                "SELECT created, tokens FROM reservations WHERE tenant=? ORDER BY created",
                (tenant,),
            ).fetchall()
            total = sum(row[1] for row in rows)
            if total + tokens > self.limit:
                remaining = total
                retry = self.window
                for created, used in rows:
                    remaining -= used
                    if remaining + tokens <= self.limit:
                        retry = max(1, math.ceil(created + self.window - now))
                        break
                # Commit expiry cleanup even when admission is rejected.
                db.commit()
                raise RateLimited(math.ceil(retry))
            db.execute(
                "INSERT INTO reservations(tenant, created, tokens) VALUES (?, ?, ?)",
                (tenant, now, tokens),
            )
