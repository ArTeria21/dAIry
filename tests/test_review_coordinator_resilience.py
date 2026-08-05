from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import time
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.services import reviews


def test_AC_review_coordinator_retries_after_one_failed_pass_and_remains_cancellable(
    tmp_path,
):
    retried = asyncio.Event()

    class FlakyCoordinator(reviews.ReviewCoordinator):
        attempts = 0

        async def reconcile_once(self, *, now=None) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient generation failure")
            retried.set()

    class IdleRunner:
        async def run_next(self) -> bool:
            return False

    async def scenario() -> None:
        coordinator = FlakyCoordinator(
            vault=tmp_path,
            store=reviews.ReviewStore(tmp_path / "reviews.sqlite3"),
            timezone=ZoneInfo("Europe/Vienna"),
            weekly_time=time(9),
            monthly_time=time(10),
            runner=IdleRunner(),
            poll_interval_seconds=0,
        )
        task = asyncio.create_task(coordinator.run_forever())
        try:
            await asyncio.wait_for(retried.wait(), timeout=0.25)
            assert coordinator.attempts >= 2
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, RuntimeError):
                await task

    asyncio.run(scenario())
