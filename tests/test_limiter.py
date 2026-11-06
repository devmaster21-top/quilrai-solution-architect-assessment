import asyncio
import sqlite3
from contextlib import closing

import pytest

from quilr_gateway.limiter import RateLimited, TokenLimiter


async def test_window_boundary_tenant_isolation_and_persistence(tmp_path):
    now = [100.0]
    path = str(tmp_path / "limiter.sqlite3")
    limiter = TokenLimiter(path, limit=100, clock=lambda: now[0])
    await limiter.reserve("a", 70)
    now[0] = 110
    await limiter.reserve("a", 30)
    await limiter.reserve("b", 100)
    restarted = TokenLimiter(path, limit=100, clock=lambda: now[0])
    with pytest.raises(RateLimited) as error:
        await restarted.reserve("a", 1)
    assert error.value.retry_after == 50
    now[0] = 160
    await restarted.reserve("a", 70)
    with pytest.raises(RateLimited):
        await restarted.reserve("a", 1)
    now[0] = 170
    await restarted.reserve("a", 30)


async def test_atomic_reservations_across_independent_instances(tmp_path):
    path = str(tmp_path / "concurrency.sqlite3")
    instances = [TokenLimiter(path, limit=100) for _ in range(5)]
    results = await asyncio.gather(
        *(instances[i % 5].reserve("same-tenant", 10) for i in range(50)),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 10
    assert sum(isinstance(result, RateLimited) for result in results) == 40
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT SUM(tokens) FROM reservations").fetchone()[0] == 100
        assert db.execute("SELECT tenant FROM reservations LIMIT 1").fetchone()[0] != "same-tenant"


async def test_retry_after_waits_for_enough_tokens(tmp_path):
    now = [100.0]
    limiter = TokenLimiter(str(tmp_path / "x.sqlite3"), limit=100, clock=lambda: now[0])
    await limiter.reserve("a", 20)
    now[0] = 110
    await limiter.reserve("a", 80)
    now[0] = 120
    with pytest.raises(RateLimited) as error:
        await limiter.reserve("a", 50)
    assert error.value.retry_after == 50


async def test_oversized_request_and_invalid_reservation(tmp_path):
    limiter = TokenLimiter(str(tmp_path / "x.sqlite3"), limit=100)
    with pytest.raises(RateLimited):
        await limiter.reserve("a", 101)
    for tokens in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            await limiter.reserve("a", tokens)
    await limiter.reserve("a", 100)


def test_requires_on_disk_database():
    with pytest.raises(ValueError):
        TokenLimiter(":memory:")
