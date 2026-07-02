from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from dairy_web.analysis import (
    AnalysisCache,
    AnalysisService,
    ClusterSummary,
    MapPoint,
    MapSnapshot,
    RebuildResult,
)
from dairy_web.app import create_app
from dairy_web.auth import AuthService, AuthSettings, SessionSigner
from dairy_web.data_access import DayRecord, NoteRecord
from dairy_web.resurface import resurface_weight


class FakeVerifier:
    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "argon2-hash" and password == "secret"


class FakeStore:
    def __init__(self, *, notes: list[NoteRecord], days: list[DayRecord]):
        self.notes = notes
        self.days = days

    def list_notes(self) -> list[NoteRecord]:
        return list(self.notes)

    def note_content_hashes(self) -> dict[str, str]:
        return {}

    def get_note(self, note_id: str) -> NoteRecord | None:
        return next((note for note in self.notes if note.id == note_id), None)

    def list_days(self) -> list[DayRecord]:
        return list(self.days)

    def get_day(self, day: str) -> DayRecord | None:
        return next((item for item in self.days if item.date == day), None)


class FakeAnalysis:
    def __init__(self, snapshot: MapSnapshot):
        self.snapshot = snapshot
        self.rebuild_calls = 0

    def get_map(self) -> MapSnapshot:
        return self.snapshot

    def rebuild(self) -> RebuildResult:
        self.rebuild_calls += 1
        return RebuildResult(
            signature="sig-rebuilt",
            computed_at="2026-06-17T12:30:00+00:00",
            n_points=1,
            n_clusters=1,
            n_noise=0,
        )


class FakeProjector:
    def project(self, vectors):
        return [(float(index), float(index + 10)) for index, _ in enumerate(vectors)]


class FakeReducer:
    def reduce(self, vectors):
        return [
            [float(index + value) for value in range(10)]
            for index, _ in enumerate(vectors)
        ]


class FakeClusterer:
    def cluster(self, vectors):
        return [5 for _ in vectors]


class FailingLabeler:
    def label_clusters(self, clusters):
        raise RuntimeError("labeler unavailable")


def make_note(note_id: str = "2026-06-16T21:55") -> NoteRecord:
    return NoteRecord(
        id=note_id,
        date=note_id[:10],
        ts=note_id[11:16],
        note_path=f"{note_id[:4]}/{note_id[5:7]}/{note_id[:10]}.md",
        gist="The user reflected on language practice.",
        mood="calm",
        mood_confidence=0.82,
        topics=["learning", "reflection"],
        mood_evidence="The note sounds calm and reflective.",
        embedding=[0.1, 0.2, 0.3],
    )


def make_day(
    day: str = "2026-06-16",
    *,
    mood: str = "calm",
    confidence: float = 0.82,
) -> DayRecord:
    return DayRecord(
        date=day,
        summary="A processed summary of the day.",
        mood=mood,
        mood_confidence=confidence,
        key_topics=["learning", "reflection"],
        weekday="Tuesday",
        is_weekend=False,
        season="summer",
        facts={
            "sport": True,
            "reading": None,
            "purchases": False,
            "eating_outside": None,
            "deep_focus": True,
            "sleep_quality": 4,
        },
    )


def build_client(tmp_path: Path) -> tuple[TestClient, FakeAnalysis]:
    note = make_note()
    day = make_day()
    vault_file = tmp_path / note.note_path
    vault_file.parent.mkdir(parents=True)
    vault_file.write_text(
        "\n".join(
            [
                "# 2026-06-16",
                "",
                "## 21:55 — text",
                "",
                "Private raw transcript.",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = MapSnapshot(
        signature="sig-1",
        computed_at="2026-06-17T12:00:00+00:00",
        points=[
            MapPoint(
                id=note.id,
                x=1.0,
                y=2.0,
                cluster_id=3,
                mood=note.mood,
                topics=note.topics,
                gist=note.gist,
                date=note.date,
                ts=note.ts,
            )
        ],
        clusters=[
            ClusterSummary(
                id=3,
                label="Language Practice",
                size=1,
                dominant_topics=["learning"],
            )
        ],
        n_noise=0,
    )
    analysis = FakeAnalysis(snapshot)
    auth = AuthService(
        settings=AuthSettings(
            username="artem",
            password_argon2="argon2-hash",
            session_secret="test-secret",
            rate_limit_attempts=3,
            rate_limit_window_seconds=60,
        ),
        verifier=FakeVerifier(),
        signer=SessionSigner("test-secret"),
    )
    app = create_app(
        store=FakeStore(notes=[note], days=[day]),
        analysis=analysis,
        auth=auth,
        vault_dir=tmp_path,
        cookie_secure=True,
    )
    return TestClient(app, base_url="https://testserver"), analysis


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "artem", "password": "secret"},
    )
    assert response.status_code == 200


def test_AC_5_routes_are_auth_gated_and_login_sets_secure_session_cookie(tmp_path):
    client, _ = build_client(tmp_path)

    assert client.get("/api/map").status_code == 401

    response = client.post(
        "/api/auth/login",
        json={"username": "artem", "password": "secret"},
    )

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "dairy_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert client.get("/api/auth/me").json() == {"username": "artem"}
    assert client.get("/api/map").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/map").status_code == 401


def test_AC_6_map_payload_contains_gist_but_never_raw_text_and_rebuild_is_protected(tmp_path):
    client, analysis = build_client(tmp_path)
    login(client)

    payload = client.get("/api/map").json()
    rebuild = client.post("/api/rebuild").json()

    assert payload["signature"] == "sig-1"
    assert payload["n_noise"] == 0
    assert payload["points"][0]["gist"] == "The user reflected on language practice."
    assert "raw_text" not in json.dumps(payload)
    assert payload["clusters"] == [
        {
            "id": 3,
            "label": "Language Practice",
            "size": 1,
            "dominant_topics": ["learning"],
        }
    ]
    assert rebuild == {
        "signature": "sig-rebuilt",
        "computed_at": "2026-06-17T12:30:00+00:00",
        "n_points": 1,
        "n_clusters": 1,
        "n_noise": 0,
    }
    assert analysis.rebuild_calls == 1


def test_AC_3_rebuild_endpoint_survives_labeler_failure_with_static_labels(tmp_path):
    note_list = [
        make_note(f"2026-06-{index + 1:02d}T10:00") for index in range(15)
    ]
    store = FakeStore(notes=note_list, days=[make_day()])
    analysis = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=FakeProjector(),
        reducer=FakeReducer(),
        clusterer=FakeClusterer(),
        labeler=FailingLabeler(),
    )
    auth = AuthService(
        settings=AuthSettings(
            username="artem",
            password_argon2="argon2-hash",
            session_secret="test-secret",
            rate_limit_attempts=3,
            rate_limit_window_seconds=60,
        ),
        verifier=FakeVerifier(),
        signer=SessionSigner("test-secret"),
    )
    app = create_app(
        store=store,
        analysis=analysis,
        auth=auth,
        vault_dir=tmp_path,
        cookie_secure=True,
    )
    client = TestClient(app, base_url="https://testserver")

    login(client)
    rebuild = client.post("/api/rebuild")
    payload = client.get("/api/map").json()

    assert rebuild.status_code == 200
    assert rebuild.json()["n_clusters"] == 1
    assert payload["clusters"][0]["label"]
    assert payload["n_noise"] == 0


def test_AC_6_note_detail_is_the_only_payload_with_raw_text_and_missing_note_is_404(tmp_path):
    client, _ = build_client(tmp_path)
    login(client)

    detail = client.get("/api/notes/2026-06-16T21:55").json()
    missing = client.get("/api/notes/2026-06-18T11:00")

    assert detail["raw_text"] == "Private raw transcript."
    assert detail["day_summary"] == "A processed summary of the day."
    assert detail["mood_evidence"] == "The note sounds calm and reflective."
    assert missing.status_code == 404
    assert "2026/06" not in missing.text


def test_AC_6_calendar_topics_and_resurface_payloads_do_not_include_raw_text(tmp_path):
    client, _ = build_client(tmp_path)
    login(client)

    calendar = client.get("/api/calendar?from=2026-06-01&to=2026-06-30").json()
    timeline = client.get("/api/topics/timeline?bucket=week").json()
    resurface = client.get("/api/resurface").json()

    assert calendar["days"][0]["facts"]["sport"] is True
    assert timeline["buckets"] == [
        {
            "period": "2026-W25",
            "counts": {"learning": 1, "reflection": 1},
        }
    ]
    assert resurface["day"]["summary"] == "A processed summary of the day."
    assert "raw_text" not in json.dumps(calendar)
    assert "raw_text" not in json.dumps(timeline)
    assert "raw_text" not in json.dumps(resurface)


def test_AC_7_resurface_weight_biases_emotionally_salient_days():
    neutral = make_day(mood="neutral", confidence=1.0)
    low_sadness = replace(make_day(mood="sadness", confidence=0.2), date="2026-06-17")
    high_sadness = replace(make_day(mood="sadness", confidence=0.9), date="2026-06-18")

    assert resurface_weight(high_sadness) > resurface_weight(low_sadness)
    assert resurface_weight(high_sadness) > resurface_weight(neutral)
