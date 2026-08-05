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
import dairy_web.data_access as data_access
from dairy_web.data_access import DayRecord, NoteRecord
from dairy_web.resurface import resurface_weight
from dairy_web.vault_reader import raw_text_sha256


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


class FakeEditClient:
    def __init__(self, responses: list[tuple[int, dict[str, object]]]):
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []
        self.delete_calls: list[dict[str, str]] = []

    def replace_text(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]:
        self.calls.append(payload)
        if not self.responses:
            return 500, {"detail": "unexpected"}
        return self.responses.pop(0)

    def delete_note(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]:
        self.delete_calls.append(payload)
        if not self.responses:
            return 500, {"detail": "unexpected"}
        return self.responses.pop(0)


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


def build_store_client(
    tmp_path: Path,
    *,
    notes: list[NoteRecord],
    days: list[DayRecord],
    edit_client=None,
) -> TestClient:
    analysis = FakeAnalysis(
        MapSnapshot(
            signature="empty",
            computed_at="2026-06-17T12:00:00+00:00",
            points=[],
            clusters=[],
            n_noise=0,
        )
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
        store=FakeStore(notes=notes, days=days),
        analysis=analysis,
        auth=auth,
        vault_dir=tmp_path,
        cookie_secure=True,
        edit_client=edit_client,
    )
    return TestClient(app, base_url="https://testserver")


def write_day(vault_dir: Path, raw_date: str, lines: list[str]) -> None:
    path = vault_dir / raw_date[:4] / raw_date[5:7] / f"{raw_date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_day_reader_client(tmp_path: Path) -> TestClient:
    write_day(
        tmp_path,
        "2026-06-15",
        [
            "# 2026-06-15",
            "",
            "## 08:00 — text",
            "Vault-only raw text.",
        ],
    )
    write_day(
        tmp_path,
        "2026-06-16",
        [
            "# 2026-06-16",
            "",
            "## 09:00 — text",
            "First raw note.",
            "",
            "## 09:00 — voice",
            "Second duplicate raw.",
            "",
            "## June 16 21:55",
            "Unmatched raw text.",
        ],
    )
    write_day(
        tmp_path,
        "2026-06-18",
        [
            "# 2026-06-18",
            "",
            "A day page without note blocks.",
        ],
    )
    notes = [
        replace(
            make_note("2026-06-16T09:00"),
            gist="First enriched note.",
            mood="calm",
            topics=["morning", "work"],
        ),
        replace(
            make_note("2026-06-16T09:00#2"),
            gist="Second enriched note.",
            mood="joy",
            topics=["voice"],
        ),
    ]
    days = [
        make_day("2026-06-16", mood="calm"),
        make_day("2026-06-18", mood="neutral", confidence=0.5),
    ]
    return build_store_client(tmp_path, notes=notes, days=days)


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
            "description": "",
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


def test_AC_7_map_and_rebuild_return_explicit_503_while_semantic_index_builds(tmp_path):
    client, analysis = build_client(tmp_path)

    def building():
        raise getattr(data_access, "SemanticIndexBuilding")()

    analysis.get_map = building
    analysis.rebuild = building
    login(client)

    map_response = client.get("/api/map")
    rebuild_response = client.post("/api/rebuild")

    assert (map_response.status_code, map_response.json()) == (
        503,
        {"detail": "semantic_index_building"},
    )
    assert (rebuild_response.status_code, rebuild_response.json()) == (
        503,
        {"detail": "semantic_index_building"},
    )


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
    assert detail["raw_text_sha256"] == raw_text_sha256("Private raw transcript.")
    assert detail["day_summary"] == "A processed summary of the day."
    assert detail["mood_evidence"] == "The note sounds calm and reflective."
    assert missing.status_code == 404
    assert "2026/06" not in missing.text


def test_note_detail_reads_duplicate_timestamp_note_by_exact_encoded_id(tmp_path):
    client = build_day_reader_client(tmp_path)
    login(client)

    response = client.get("/api/notes/2026-06-16T09:00%232")
    payload = response.json()

    assert response.status_code == 200
    assert payload["id"] == "2026-06-16T09:00#2"
    assert payload["raw_text"] == "Second duplicate raw."
    assert payload["raw_text_sha256"] == raw_text_sha256("Second duplicate raw.")
    assert payload["gist"] == "Second enriched note."


def test_sprint_3_day_detail_reads_vault_and_joins_enrichment_by_bot_note_ids(tmp_path):
    client = build_day_reader_client(tmp_path)
    login(client)

    response = client.get("/api/days/2026-06-16")
    payload = response.json()

    assert response.status_code == 200
    assert payload["date"] == "2026-06-16"
    assert payload["prev_date"] == "2026-06-15"
    assert payload["next_date"] == "2026-06-18"
    assert payload["day"]["summary"] == "A processed summary of the day."
    assert [note["id"] for note in payload["notes"]] == [
        "2026-06-16T09:00",
        "2026-06-16T09:00#2",
        "2026-06-16T21:55",
    ]
    assert payload["notes"][0]["raw_text"] == "First raw note."
    assert payload["notes"][0]["mood"] == "calm"
    assert payload["notes"][0]["topics"] == ["morning", "work"]
    assert payload["notes"][1]["kind"] == "voice"
    assert payload["notes"][1]["gist"] == "Second enriched note."
    assert payload["notes"][2] == {
        "id": "2026-06-16T21:55",
        "ts": "21:55",
        "kind": None,
        "heading_display": "June 16 21:55",
        "raw_text": "Unmatched raw text.",
        "raw_text_sha256": raw_text_sha256("Unmatched raw text."),
        "mood": None,
        "topics": [],
        "gist": None,
    }


def test_sprint_3_day_detail_allows_vault_only_days_and_empty_note_days(tmp_path):
    client = build_day_reader_client(tmp_path)
    login(client)

    vault_only = client.get("/api/days/2026-06-15").json()
    no_blocks = client.get("/api/days/2026-06-18").json()

    assert vault_only["day"] is None
    assert vault_only["notes"][0]["raw_text"] == "Vault-only raw text."
    assert vault_only["notes"][0]["mood"] is None
    assert no_blocks["day"]["mood"] == "neutral"
    assert no_blocks["notes"] == []


def test_sprint_3_month_index_has_counts_but_no_raw_text_or_paths(tmp_path):
    client = build_day_reader_client(tmp_path)
    login(client)

    response = client.get("/api/days?month=2026-06")
    payload = response.json()

    assert response.status_code == 200
    assert payload == {
        "days": [
            {"date": "2026-06-15", "note_count": 1, "mood": None},
            {"date": "2026-06-16", "note_count": 3, "mood": "calm"},
            {"date": "2026-06-18", "note_count": 0, "mood": "neutral"},
        ]
    }
    serialized = json.dumps(payload)
    assert "raw_text" not in serialized
    assert "Vault-only raw text" not in serialized
    assert "note_path" not in serialized
    assert "2026/06" not in serialized


def test_day_detail_and_month_index_skip_empty_duplicate_blocks(tmp_path):
    write_day(
        tmp_path,
        "2026-06-20",
        [
            "# 2026-06-20",
            "",
            "## 10:00",
            "",
            "<!-- dairy:note-enrichment -->",
            "mood:: calm · topics:: work",
            "",
            "## 10:00",
            "",
            "second block text",
            "",
            "## 10:00",
            "",
            "third block text",
        ],
    )
    notes = [
        replace(
            make_note("2026-06-20T10:00"),
            gist="Second enriched note.",
            topics=["second"],
        ),
        replace(
            make_note("2026-06-20T10:00#2"),
            gist="Third enriched note.",
            topics=["third"],
        ),
    ]
    client = build_store_client(tmp_path, notes=notes, days=[])
    login(client)

    day_payload = client.get("/api/days/2026-06-20").json()
    month_payload = client.get("/api/days?month=2026-06").json()

    assert [(note["id"], note["raw_text"], note["gist"]) for note in day_payload["notes"]] == [
        ("2026-06-20T10:00", "second block text", "Second enriched note."),
        ("2026-06-20T10:00#2", "third block text", "Third enriched note."),
    ]
    assert month_payload == {
        "days": [{"date": "2026-06-20", "note_count": 2, "mood": None}]
    }


def test_sprint_3_day_endpoints_are_auth_gated_and_latest_uses_last_existing_day(tmp_path):
    client = build_day_reader_client(tmp_path)

    assert client.get("/api/days/latest").status_code == 401
    assert client.get("/api/days/2026-06-16").status_code == 401
    assert client.get("/api/days?month=2026-06").status_code == 401

    login(client)
    latest = client.get("/api/days/latest").json()

    assert latest["date"] == "2026-06-18"
    assert latest["prev_date"] == "2026-06-16"
    assert latest["next_date"] is None


def test_sprint_3_day_endpoint_validation_and_missing_days_are_sanitized(tmp_path):
    client = build_day_reader_client(tmp_path)
    login(client)

    invalid_day = client.get("/api/days/2026-02-30")
    missing_day = client.get("/api/days/2026-06-17")
    invalid_month = client.get("/api/days?month=2026-13")
    empty_latest = build_store_client(tmp_path / "empty", notes=[], days=[])
    login(empty_latest)

    assert invalid_day.status_code == 422
    assert missing_day.status_code == 404
    assert "2026/06" not in missing_day.text
    assert str(tmp_path) not in missing_day.text
    assert invalid_month.status_code == 422
    assert empty_latest.get("/api/days/latest").status_code == 404


def test_sprint_4_put_note_proxies_to_bot_with_db_note_path_and_auth(tmp_path):
    note = make_note()
    fake_edit = FakeEditClient([(200, {"new_sha256": "new-hash"})])
    client = build_store_client(tmp_path, notes=[note], days=[], edit_client=fake_edit)

    assert client.put(
        f"/api/notes/{note.id}",
        json={"new_text": "Updated", "expected_sha256": "old-hash"},
    ).status_code == 401

    login(client)
    response = client.put(
        f"/api/notes/{note.id}",
        json={
            "new_text": "Updated",
            "expected_sha256": "old-hash",
            "note_path": "evil.md",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"id": note.id, "new_sha256": "new-hash"}
    assert fake_edit.calls == [
        {
            "note_id": note.id,
            "note_path": note.note_path,
            "expected_sha256": "old-hash",
            "new_text": "Updated",
        }
    ]


def test_sprint_4_put_note_validation_and_disabled_editing(tmp_path):
    note = make_note()
    fake_edit = FakeEditClient([(200, {"new_sha256": "unused"})])
    client = build_store_client(tmp_path, notes=[note], days=[], edit_client=fake_edit)
    disabled = build_store_client(tmp_path / "disabled", notes=[note], days=[])
    login(client)
    login(disabled)

    invalid = client.put(
        f"/api/notes/{note.id}",
        json={"new_text": "bad\n## 12:34", "expected_sha256": "old-hash"},
    )
    disabled_response = disabled.put(
        f"/api/notes/{note.id}",
        json={"new_text": "Updated", "expected_sha256": "old-hash"},
    )

    assert invalid.status_code == 422
    assert fake_edit.calls == []
    assert disabled_response.status_code == 502
    assert disabled_response.json()["detail"] == "editing disabled"


def test_sprint_4_put_note_maps_bot_statuses(tmp_path):
    note = make_note()

    for status in (404, 409, 422):
        fake_edit = FakeEditClient([(status, {"detail": f"bot {status}"})])
        client = build_store_client(tmp_path / str(status), notes=[note], days=[], edit_client=fake_edit)
        login(client)

        response = client.put(
            f"/api/notes/{note.id}",
            json={"new_text": "Updated", "expected_sha256": "old-hash"},
        )

        assert response.status_code == status
        assert response.json()["detail"] == f"bot {status}"

    fake_edit = FakeEditClient([(500, {"detail": "boom"})])
    client = build_store_client(tmp_path / "500", notes=[note], days=[], edit_client=fake_edit)
    login(client)
    response = client.put(
        f"/api/notes/{note.id}",
        json={"new_text": "Updated", "expected_sha256": "old-hash"},
    )
    assert response.status_code == 502


def test_sprint_7_delete_note_proxies_to_bot_with_db_note_path_and_auth(tmp_path):
    note = make_note()
    fake_edit = FakeEditClient([(200, {"deleted": True})])
    client = build_store_client(tmp_path, notes=[note], days=[], edit_client=fake_edit)

    assert client.request(
        "DELETE",
        f"/api/notes/{note.id}",
        json={"expected_sha256": "old-hash"},
    ).status_code == 401

    login(client)
    response = client.request(
        "DELETE",
        f"/api/notes/{note.id}",
        json={"expected_sha256": "old-hash", "note_path": "evil.md"},
    )

    assert response.status_code == 200
    assert response.json() == {"id": note.id, "deleted": True}
    assert fake_edit.delete_calls == [
        {
            "note_id": note.id,
            "note_path": note.note_path,
            "expected_sha256": "old-hash",
        }
    ]


def test_sprint_7_delete_note_validation_missing_note_and_disabled_editing(tmp_path):
    note = make_note()
    fake_edit = FakeEditClient([(200, {"deleted": True})])
    client = build_store_client(tmp_path, notes=[note], days=[], edit_client=fake_edit)
    disabled = build_store_client(tmp_path / "disabled", notes=[note], days=[])
    login(client)
    login(disabled)

    invalid = client.request("DELETE", f"/api/notes/{note.id}", json={})
    missing = client.request(
        "DELETE",
        "/api/notes/2026-06-17T10:00",
        json={"expected_sha256": "old-hash"},
    )
    disabled_response = disabled.request(
        "DELETE",
        f"/api/notes/{note.id}",
        json={"expected_sha256": "old-hash"},
    )

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert fake_edit.delete_calls == []
    assert disabled_response.status_code == 502
    assert disabled_response.json()["detail"] == "editing disabled"


def test_sprint_7_delete_note_maps_bot_statuses(tmp_path):
    note = make_note()

    for status in (404, 409, 422):
        fake_edit = FakeEditClient([(status, {"detail": f"bot {status}"})])
        client = build_store_client(tmp_path / f"delete-{status}", notes=[note], days=[], edit_client=fake_edit)
        login(client)

        response = client.request(
            "DELETE",
            f"/api/notes/{note.id}",
            json={"expected_sha256": "old-hash"},
        )

        assert response.status_code == status
        assert response.json()["detail"] == f"bot {status}"

    fake_edit = FakeEditClient([(500, {"detail": "boom"})])
    client = build_store_client(tmp_path / "delete-500", notes=[note], days=[], edit_client=fake_edit)
    login(client)
    response = client.request(
        "DELETE",
        f"/api/notes/{note.id}",
        json={"expected_sha256": "old-hash"},
    )
    assert response.status_code == 502


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
            "total": 1,
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
