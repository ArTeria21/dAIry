from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from dairy_bot.services.edit_api import create_edit_app
from dairy_bot.services.reviews import ReviewStore


async def _requests(app, specs):
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        results = []
        for spec in specs:
            method = spec.get("method", "post")
            response = await getattr(client, method)(
                spec["endpoint"],
                headers={"X-Edit-Token": spec.get("token", "secret")},
            )
            results.append((response.status, await response.json()))
        return results
    finally:
        await client.close()


def _app(tmp_path):
    store = ReviewStore(tmp_path / "reviews.sqlite3")
    generation = SimpleNamespace(
        current_source_hash=lambda kind, period: "source-v1"
    )
    runtime = SimpleNamespace(store=store, generation_service=generation)
    settings = SimpleNamespace(
        journal_dir=tmp_path,
        edit_api_token=SecretStr("secret"),
    )
    return create_edit_app(settings, SimpleNamespace(), review_runtime=runtime), store


def test_AC_4b_regenerate_is_active_idempotent_but_new_after_completion(tmp_path):
    app, store = _app(tmp_path)

    async def scenario():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            first_response = await client.post(
                "/internal/reviews/week/2026-07-26/regenerate",
                headers={"X-Edit-Token": "secret"},
            )
            first = await first_response.json()
            duplicate_response = await client.post(
                "/internal/reviews/week/2026-07-26/regenerate",
                headers={"X-Edit-Token": "secret"},
            )
            duplicate = await duplicate_response.json()
            job_response = await client.get(
                f"/internal/review-jobs/{first['job_id']}",
                headers={"X-Edit-Token": "secret"},
            )
            job = await job_response.json()
            store.set_job_status(first["job_id"], "complete")
            next_response = await client.post(
                "/internal/reviews/week/2026-07-26/regenerate",
                headers={"X-Edit-Token": "secret"},
            )
            next_job = await next_response.json()
            return (
                first_response.status,
                first,
                duplicate_response.status,
                duplicate,
                job_response.status,
                job,
                next_response.status,
                next_job,
            )
        finally:
            await client.close()

    (
        first_status,
        first,
        duplicate_status,
        duplicate,
        job_status,
        job,
        next_status,
        next_job,
    ) = asyncio.run(scenario())

    assert first_status == duplicate_status == next_status == 202
    assert duplicate == first
    assert job_status == 200 and job["status"] == "pending"
    assert next_job["job_id"] != first["job_id"]
    assert all(job.reason.startswith("regenerate") for job in store.list_jobs())


def test_ERR_4b_regenerate_validates_auth_kind_period_and_missing_jobs(tmp_path):
    app, store = _app(tmp_path)

    unauthorized, invalid_kind, invalid_period, missing = asyncio.run(
        _requests(
            app,
            [
                {
                    "endpoint": "/internal/reviews/week/2026-07-26/regenerate",
                    "token": "wrong",
                },
                {"endpoint": "/internal/reviews/year/2026/regenerate"},
                {"endpoint": "/internal/reviews/month/2026-13/regenerate"},
                {"endpoint": "/internal/review-jobs/999", "method": "get"},
            ],
        )
    )

    assert unauthorized[0] == 401
    assert invalid_kind[0] == invalid_period[0] == 422
    assert missing[0] == 404
    assert store.list_jobs() == []
