from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dairy_web.app import create_app
from dairy_web.auth import AuthService, AuthSettings, SessionSigner


class _Verifier:
    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "hash" and password == "secret"


class _EnrichmentStore:
    def list_notes(self):
        return []

    def note_content_hashes(self):
        return {}

    def get_note(self, note_id):
        return None

    def list_days(self):
        return []

    def get_day(self, day):
        return None


class _Analysis:
    def get_map(self):
        return SimpleNamespace(
            signature="empty", computed_at="now", points=[], clusters=[], n_noise=0
        )

    def rebuild(self):
        return SimpleNamespace(
            signature="empty",
            computed_at="now",
            n_points=0,
            n_clusters=0,
            n_noise=0,
        )


class _ReviewStore:
    def __init__(self, record: dict, sources: list[dict]):
        self.record = record
        self.sources = sources

    def list_reviews(self, kind: str):
        if kind != self.record["kind"] or self.record["status"] != "ready":
            return []
        return [self.record]

    def get_review(self, kind: str, period: str):
        if (kind, period) != (self.record["kind"], self.record["period"]):
            return None
        return self.record if self.record["status"] == "ready" else None

    def list_sources(self, kind: str, period: str):
        return list(self.sources)


class _ReviewClient:
    def __init__(self):
        self.regenerate_calls: list[tuple[str, str]] = []
        self.job_calls: list[int] = []

    def regenerate(self, kind: str, period: str):
        self.regenerate_calls.append((kind, period))
        return 202, {"job_id": 73, "status": "pending"}

    def get_job(self, job_id: int):
        self.job_calls.append(job_id)
        return 200, {"job_id": job_id, "status": "running"}


def _record(image_path: str | None) -> dict:
    return {
        "kind": "week",
        "period": "2026-07-26",
        "start_date": "2026-07-26",
        "end_date": "2026-08-01",
        "status": "ready",
        "title": "A week of recalibration",
        "payload": {
            "paragraphs": [
                {
                    "text": "A grounded paragraph.",
                    "evidence_refs": [
                        "diary:2026-07-31T09:00",
                        "vault:projects/idea.md#overview",
                        "parallel:secret",
                    ],
                }
            ],
            "counts": {"entry_count": 3, "active_days": 2},
            "visual_brief": "internal image prompt",
            "raw_diary": "must never escape",
            "parallel_queries": ["must never escape"],
        },
        "telegram_caption": "Private Telegram variant",
        "reflection_question": "What changed?",
        "safety_note": None,
        "image_path": image_path,
        "image_alt": "An abstract weekly poster",
        "language": "EN",
        "model": "test/model",
        "source_hash": "private-hash",
        "version": 2,
        "created_at": "2026-08-02T09:00:00+02:00",
        "updated_at": "2026-08-02T09:05:00+02:00",
    }


def _sources() -> list[dict]:
    return [
        {
            "source_id": "diary:2026-07-31T09:00",
            "source_type": "diary",
            "label": "31 Jul, 09:00",
            "position": 0,
        },
        {
            "source_id": "vault:projects/idea.md#overview",
            "source_type": "vault",
            "label": "/absolute/private/vault/projects/idea.md · Overview",
            "position": 1,
        },
        {
            "source_id": "parallel:secret",
            "source_type": "external",
            "label": "Hidden web source",
            "position": 2,
        },
    ]


def _client(
    tmp_path: Path,
    *,
    record: dict | None = None,
    regeneration_enabled: bool = True,
):
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    record = record or _record(None)
    review_client = _ReviewClient() if regeneration_enabled else None
    auth = AuthService(
        settings=AuthSettings(
            username="artem",
            password_argon2="hash",
            session_secret="secret",
            rate_limit_attempts=3,
            rate_limit_window_seconds=60,
        ),
        verifier=_Verifier(),
        signer=SessionSigner("secret"),
    )
    app = create_app(
        store=_EnrichmentStore(),
        analysis=_Analysis(),
        auth=auth,
        vault_dir=tmp_path,
        cookie_secure=True,
        review_store=_ReviewStore(record, _sources()),
        review_assets_dir=assets,
        review_client=review_client,
    )
    return TestClient(app, base_url="https://testserver"), review_client, assets


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "artem", "password": "secret"}
    )
    assert response.status_code == 200


def test_AC_4_1_review_routes_are_auth_gated(tmp_path):
    client, _, _ = _client(tmp_path)

    assert client.get("/api/reviews/capabilities").status_code == 401
    assert client.get("/api/reviews?kind=week").status_code == 401
    assert client.get("/api/reviews/week/2026-07-26").status_code == 401
    assert client.get("/api/reviews/week/2026-07-26/image").status_code == 401
    assert client.post("/api/reviews/week/2026-07-26/regenerate").status_code == 401
    assert client.get("/api/review-jobs/73").status_code == 401


def test_AC_4_1_capabilities_exactly_reflect_regeneration_client(tmp_path):
    enabled, _, _ = _client(tmp_path / "enabled")
    disabled, _, _ = _client(
        tmp_path / "disabled",
        regeneration_enabled=False,
    )
    _login(enabled)
    _login(disabled)

    assert enabled.get("/api/reviews/capabilities").json() == {
        "regenerate": True
    }
    assert disabled.get("/api/reviews/capabilities").json() == {
        "regenerate": False
    }


def test_AC_4_2_archive_and_detail_are_public_projections_with_safe_evidence(tmp_path):
    client, _, _ = _client(tmp_path)
    _login(client)

    archive = client.get("/api/reviews?kind=week")
    detail = client.get("/api/reviews/week/2026-07-26")

    assert archive.status_code == 200
    assert archive.json() == {
        "reviews": [
            {
                "kind": "week",
                "period": "2026-07-26",
                "start_date": "2026-07-26",
                "end_date": "2026-08-01",
                "title": "A week of recalibration",
                "counts": {"entry_count": 3, "active_days": 2},
                "has_image": False,
                "language": "EN",
                "version": 2,
                "updated_at": "2026-08-02T09:05:00+02:00",
            }
        ]
    }
    payload = detail.json()
    assert detail.status_code == 200
    assert payload["paragraphs"] == [
        {
            "text": "A grounded paragraph.",
            "evidence": [
                {
                    "id": "diary:2026-07-31T09:00",
                    "type": "diary",
                    "label": "31 Jul, 09:00",
                    "href": "#journal/2026-07-31",
                },
                {
                    "id": "vault:projects/idea.md#overview",
                    "type": "vault",
                    "label": "projects/idea.md",
                    "href": None,
                },
            ],
        }
    ]
    assert payload["reflection_question"] == "What changed?"
    assert payload["image"] is None
    serialized = json.dumps(payload)
    for secret in (
        "raw_diary",
        "must never escape",
        "parallel",
        "source_hash",
        "telegram_caption",
        "visual_brief",
        "/absolute/private",
    ):
        assert secret not in serialized


def test_AC_4_3_image_is_private_jpeg_with_etag_and_null_is_404(tmp_path):
    image = tmp_path / "assets" / "week-2026-07-26.jpg"
    image.parent.mkdir()
    image.write_bytes(b"\xff\xd8poster\xff\xd9")
    client, _, _ = _client(tmp_path, record=_record(str(image)))
    _login(client)

    response = client.get("/api/reviews/week/2026-07-26/image")
    cached = client.get(
        "/api/reviews/week/2026-07-26/image",
        headers={"If-None-Match": response.headers["etag"]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8poster\xff\xd9"
    assert cached.status_code == 304 and cached.content == b""

    null_client, _, _ = _client(tmp_path / "null", record=_record(None))
    _login(null_client)
    assert null_client.get("/api/reviews/week/2026-07-26/image").status_code == 404


def test_AC_4_5_immutable_job_image_is_served_only_from_assets_directory(tmp_path):
    assets = tmp_path / "assets"
    immutable = assets / "week-2026-07-26-job-73.jpg"
    assets.mkdir()
    immutable.write_bytes(b"\xff\xd8immutable-poster\xff\xd9")
    cross_container_path = "/bot-data/review_images/week-2026-07-26-job-73.jpg"
    client, _, _ = _client(
        tmp_path,
        record=_record(cross_container_path),
    )
    _login(client)

    response = client.get("/api/reviews/week/2026-07-26/image")

    assert response.status_code == 200
    assert response.content == b"\xff\xd8immutable-poster\xff\xd9"


def test_ERR_4_1_image_path_traversal_and_invalid_periods_are_rejected(tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8secret\xff\xd9")
    client, review_client, _ = _client(tmp_path, record=_record(str(outside)))
    _login(client)

    assert client.get("/api/reviews/week/2026-07-26/image").status_code == 404
    assert client.get("/api/reviews/week/not-a-date").status_code == 422
    assert client.get("/api/reviews/month/2026-13").status_code == 422
    assert client.post("/api/reviews/week/../../etc/regenerate").status_code in {404, 422}
    assert review_client.regenerate_calls == []


def test_AC_4_4_regeneration_and_job_status_proxy_are_idempotent_202(tmp_path):
    client, review_client, _ = _client(tmp_path)
    _login(client)

    first = client.post("/api/reviews/week/2026-07-26/regenerate")
    duplicate = client.post("/api/reviews/week/2026-07-26/regenerate")
    job = client.get("/api/review-jobs/73")

    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json() == {"job_id": 73, "status": "pending"}
    assert review_client.regenerate_calls == [
        ("week", "2026-07-26"),
        ("week", "2026-07-26"),
    ]
    assert job.status_code == 200
    assert job.json() == {"job_id": 73, "status": "running"}
    assert review_client.job_calls == [73]


def test_ERR_4_1_missing_bot_client_has_capability_false_and_controlled_response(
    tmp_path,
):
    client, _, _ = _client(tmp_path, regeneration_enabled=False)
    _login(client)

    capability = client.get("/api/reviews/capabilities")
    response = client.post("/api/reviews/week/2026-07-26/regenerate")

    assert capability.status_code == 200
    assert capability.json() == {"regenerate": False}
    assert response.status_code == 503
    assert response.json() == {"detail": "review_regeneration_disabled"}
