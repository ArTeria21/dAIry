from __future__ import annotations

import re
import hashlib
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Protocol
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel

from dairy_web.analysis import AnalysisCache, AnalysisService, OpenRouterClusterLabeler
from dairy_web.auth import (
    Argon2PasswordVerifier,
    AuthService,
    InvalidCredentials,
    RateLimitExceeded,
)
from dairy_web.data_access import (
    DayRecord,
    EnrichmentReadStore,
    NoteRecord,
    SemanticIndexBuilding,
)
from dairy_web.resurface import choose_resurface_day
from dairy_web.reviews import NullReviewReadStore, ReviewReadStore
from dairy_web.settings import WebSettings
from dairy_web.vault_reader import (
    DayNotFound,
    DayNoteBlock,
    ENRICHMENT_MARKER,
    ENTRY_HEADING_RE,
    NoteRawTextNotFound,
    extract_note_raw_text_by_id,
    identify_day_note_blocks,
    list_day_dates,
    raw_text_sha256,
    read_day,
)


SESSION_COOKIE = "dairy_session"


class LoginRequest(BaseModel):
    username: str
    password: str


class NoteEditRequest(BaseModel):
    new_text: str
    expected_sha256: str


class NoteDeleteRequest(BaseModel):
    expected_sha256: str


class BotEditClient(Protocol):
    def replace_text(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]: ...

    def delete_note(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]: ...


class BotReviewClient(Protocol):
    def regenerate(self, kind: str, period: str) -> tuple[int, dict[str, object]]: ...

    def get_job(self, job_id: int) -> tuple[int, dict[str, object]]: ...


class HttpBotEditClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def replace_text(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/internal/notes/replace-text",
                json=payload,
                headers={"X-Edit-Token": self.token},
            )
        return _decode_bot_response(response)

    def delete_note(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/internal/notes/delete",
                json=payload,
                headers={"X-Edit-Token": self.token},
            )
        return _decode_bot_response(response)


class HttpBotReviewClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def regenerate(self, kind: str, period: str) -> tuple[int, dict[str, object]]:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/internal/reviews/{kind}/{period}/regenerate",
                headers={"X-Edit-Token": self.token},
            )
        return _decode_bot_response(response)

    def get_job(self, job_id: int) -> tuple[int, dict[str, object]]:
        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{self.base_url}/internal/review-jobs/{job_id}",
                headers={"X-Edit-Token": self.token},
            )
        return _decode_bot_response(response)


def _decode_bot_response(response: httpx.Response) -> tuple[int, dict[str, object]]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def create_app(
    *,
    settings: WebSettings | None = None,
    store: EnrichmentReadStore | None = None,
    analysis: AnalysisService | None = None,
    auth: AuthService | None = None,
    vault_dir: Path | str | None = None,
    cookie_secure: bool | None = None,
    edit_client: BotEditClient | None = None,
    review_store: object | None = None,
    review_assets_dir: Path | str | None = None,
    review_client: BotReviewClient | None = None,
) -> FastAPI:
    if settings is None and (
        store is None or analysis is None or auth is None or vault_dir is None
    ):
        settings = _settings_from_env()

    store = store or EnrichmentReadStore(  # type: ignore[union-attr]
        settings.enrichment_db_path,
        settings.embeddings_db_path,
    )
    analysis = analysis or AnalysisService(
        store=store,
        cache=AnalysisCache(settings.analysis_cache_path),  # type: ignore[union-attr]
        labeler=OpenRouterClusterLabeler(
            model_name=settings.enrichment_model_name,  # type: ignore[union-attr]
            api_key=settings.openrouter_api_key,  # type: ignore[union-attr]
            base_url=settings.openrouter_base_url,  # type: ignore[union-attr]
            language=settings.language,  # type: ignore[union-attr]
        ),
    )
    auth = auth or AuthService(
        settings=settings.auth_settings(),  # type: ignore[union-attr]
        verifier=Argon2PasswordVerifier(),
    )
    vault_root = (
        Path(vault_dir)
        if vault_dir is not None
        else settings.vault_dir  # type: ignore[union-attr]
    )
    secure_cookie = (
        settings.cookie_secure if cookie_secure is None and settings is not None else cookie_secure
    )
    secure_cookie = True if secure_cookie is None else secure_cookie
    if (
        edit_client is None
        and settings is not None
        and settings.bot_edit_api_url
        and settings.edit_api_token
    ):
        edit_client = HttpBotEditClient(
            base_url=settings.bot_edit_api_url,
            token=settings.edit_api_token,
        )
    if review_store is None:
        review_store = (
            ReviewReadStore(settings.reviews_db_path)
            if settings is not None
            else NullReviewReadStore()
        )
    review_assets_root = (
        Path(review_assets_dir)
        if review_assets_dir is not None
        else settings.review_assets_dir
        if settings is not None
        else Path("data/review_images")
    )
    if (
        review_client is None
        and settings is not None
        and settings.bot_edit_api_url
        and settings.edit_api_token
    ):
        review_client = HttpBotReviewClient(
            base_url=settings.bot_edit_api_url,
            token=settings.edit_api_token,
        )

    app = FastAPI(title="dAIry Analytics API")

    @app.exception_handler(SemanticIndexBuilding)
    async def semantic_index_building(
        request: Request,
        exc: SemanticIndexBuilding,
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=503,
            content={"detail": "semantic_index_building"},
        )

    def current_username(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> str:
        if not session_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        username = auth.authenticate(session_token)
        if username is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return username

    @app.post("/api/auth/login")
    def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        client_id = request.client.host if request.client else "unknown"
        try:
            result = auth.login(
                username=payload.username,
                password=payload.password,
                client_id=client_id,
            )
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail="Too many login attempts") from exc
        except InvalidCredentials as exc:
            raise HTTPException(status_code=401, detail="Invalid username or password") from exc
        response.set_cookie(
            SESSION_COOKIE,
            result.session_token,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
        )
        return {"username": result.username}

    @app.post("/api/auth/logout")
    def logout(
        response: Response,
        username: str = Depends(current_username),
    ) -> dict[str, bool]:
        del username
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(username: str = Depends(current_username)) -> dict[str, str]:
        return {"username": username}

    @app.get("/api/map")
    def map_payload(username: str = Depends(current_username)) -> dict[str, object]:
        del username
        snapshot = analysis.get_map()
        return {
            "signature": snapshot.signature,
            "computed_at": snapshot.computed_at,
            "points": [asdict(point) for point in snapshot.points],
            "clusters": [asdict(cluster) for cluster in snapshot.clusters],
            "n_noise": snapshot.n_noise,
        }

    @app.get("/api/days")
    def day_index(
        month: str = Query(...),
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        valid_month = _validate_month(month)
        days = []
        for day in list_day_dates(vault_dir=vault_root):
            if not day.startswith(valid_month):
                continue
            record = store.get_day(day)
            days.append(
                {
                    "date": day,
                    "note_count": len(
                        identify_day_note_blocks(
                            blocks=read_day(vault_dir=vault_root, day=day),
                            target_date=day,
                        )
                    ),
                    "mood": None if record is None else record.mood,
                }
            )
        return {"days": days}

    @app.get("/api/days/latest")
    def latest_day(username: str = Depends(current_username)) -> dict[str, object]:
        del username
        dates = list_day_dates(vault_dir=vault_root)
        if not dates:
            raise HTTPException(status_code=404, detail="Day not found")
        return _day_payload(
            vault_dir=vault_root,
            store=store,
            target_date=dates[-1],
            dates=dates,
        )

    @app.get("/api/days/{target_date}")
    def day_detail(
        target_date: date,
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        day = target_date.isoformat()
        return _day_payload(
            vault_dir=vault_root,
            store=store,
            target_date=day,
            dates=list_day_dates(vault_dir=vault_root),
        )

    @app.get("/api/notes/{note_id}")
    def note_detail(
        note_id: str,
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        note = store.get_note(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        day = store.get_day(note.date)
        try:
            raw_text = extract_note_raw_text_by_id(
                vault_dir=vault_root,
                note_path=note.note_path,
                note_id=note.id,
            )
        except NoteRawTextNotFound as exc:
            raise HTTPException(status_code=404, detail="Note not found") from exc
        return {
            "id": note.id,
            "date": note.date,
            "ts": note.ts,
            "mood": note.mood,
            "mood_confidence": note.mood_confidence,
            "mood_evidence": note.mood_evidence,
            "topics": note.topics,
            "gist": note.gist,
            "raw_text": raw_text,
            "raw_text_sha256": raw_text_sha256(raw_text),
            "day_summary": None if day is None else day.summary,
            "note_path": note.note_path,
        }

    @app.put("/api/notes/{note_id}")
    def replace_note_text(
        note_id: str,
        payload: NoteEditRequest,
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        note = store.get_note(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        _validate_new_text(payload.new_text)
        if edit_client is None:
            raise HTTPException(status_code=502, detail="editing disabled")
        try:
            status, body = edit_client.replace_text(
                {
                    "note_id": note.id,
                    "note_path": note.note_path,
                    "expected_sha256": payload.expected_sha256,
                    "new_text": payload.new_text,
                }
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="editing disabled") from exc
        if status == 200:
            new_sha256 = body.get("new_sha256")
            if not isinstance(new_sha256, str):
                raise HTTPException(status_code=502, detail="editing disabled")
            return {"id": note.id, "new_sha256": new_sha256}
        if status in {404, 409, 422}:
            detail = body.get("detail")
            raise HTTPException(
                status_code=status,
                detail=detail if isinstance(detail, str) else "edit failed",
            )
        raise HTTPException(status_code=502, detail="editing disabled")

    @app.delete("/api/notes/{note_id}")
    def delete_note(
        note_id: str,
        payload: NoteDeleteRequest,
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        note = store.get_note(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        if edit_client is None:
            raise HTTPException(status_code=502, detail="editing disabled")
        try:
            status, body = edit_client.delete_note(
                {
                    "note_id": note.id,
                    "note_path": note.note_path,
                    "expected_sha256": payload.expected_sha256,
                }
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="editing disabled") from exc
        if status == 200 and body.get("deleted") is True:
            return {"id": note.id, "deleted": True}
        if status in {404, 409, 422}:
            detail = body.get("detail")
            raise HTTPException(
                status_code=status,
                detail=detail if isinstance(detail, str) else "delete failed",
            )
        raise HTTPException(status_code=502, detail="editing disabled")

    @app.get("/api/calendar")
    def calendar(
        username: str = Depends(current_username),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
    ) -> dict[str, object]:
        del username
        days = [
            day
            for day in store.list_days()
            if (from_ is None or day.date >= from_)
            and (to is None or day.date <= to)
        ]
        return {"days": [_calendar_day(day) for day in days]}

    @app.get("/api/topics/timeline")
    def topics_timeline(
        username: str = Depends(current_username),
        bucket: str = "week",
    ) -> dict[str, object]:
        del username
        if bucket != "week":
            raise HTTPException(status_code=400, detail="Only week bucket is supported")
        counts_by_period: dict[str, defaultdict[str, int]] = {}
        totals_by_period: defaultdict[str, int] = defaultdict(int)
        for note in store.list_notes():
            period = _week_period(note.date)
            totals_by_period[period] += 1
            counts = counts_by_period.setdefault(period, defaultdict(int))
            for topic in note.topics:
                counts[topic] += 1
        return {
            "buckets": [
                {
                    "period": period,
                    "counts": dict(counts),
                    "total": totals_by_period[period],
                }
                for period, counts in sorted(counts_by_period.items())
            ]
        }

    @app.get("/api/resurface")
    def resurface(username: str = Depends(current_username)) -> dict[str, object]:
        del username
        day = choose_resurface_day(store.list_days())
        if day is None:
            raise HTTPException(status_code=404, detail="No days available")
        return {
            "day": {
                "date": day.date,
                "weekday": day.weekday,
                "mood": day.mood,
                "key_topics": day.key_topics,
                "summary": day.summary,
            }
        }

    @app.post("/api/rebuild")
    def rebuild(username: str = Depends(current_username)) -> dict[str, object]:
        del username
        return asdict(analysis.rebuild())

    @app.get("/api/reviews")
    def review_archive(
        kind: str = Query(...),
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        valid_kind = _validate_review_kind(kind)
        records = review_store.list_reviews(valid_kind)  # type: ignore[attr-defined]
        return {"reviews": [_review_archive_item(record) for record in records]}

    @app.get("/api/reviews/capabilities")
    def review_capabilities(
        username: str = Depends(current_username),
    ) -> dict[str, bool]:
        del username
        return {"regenerate": review_client is not None}

    @app.get("/api/reviews/{kind}/{period}")
    def review_detail(
        kind: str,
        period: str,
        username: str = Depends(current_username),
    ) -> dict[str, object]:
        del username
        valid_kind, valid_period = _validate_review_period(kind, period)
        record = review_store.get_review(valid_kind, valid_period)  # type: ignore[attr-defined]
        if record is None:
            raise HTTPException(status_code=404, detail="Review not found")
        sources = review_store.list_sources(valid_kind, valid_period)  # type: ignore[attr-defined]
        return _review_detail_payload(record, sources)

    @app.get("/api/reviews/{kind}/{period}/image")
    def review_image(
        kind: str,
        period: str,
        request: Request,
        username: str = Depends(current_username),
    ) -> Response:
        del username
        valid_kind, valid_period = _validate_review_period(kind, period)
        record = review_store.get_review(valid_kind, valid_period)  # type: ignore[attr-defined]
        if record is None or not record.get("image_path"):
            raise HTTPException(status_code=404, detail="Review image not found")
        path = _resolve_review_image(
            review_assets_root,
            str(record["image_path"]),
            expected_stem=f"{valid_kind}-{valid_period}",
        )
        try:
            content = path.read_bytes()
        except OSError as error:
            raise HTTPException(status_code=404, detail="Review image not found") from error
        if not content.startswith(b"\xff\xd8") or not content.endswith(b"\xff\xd9"):
            raise HTTPException(status_code=404, detail="Review image not found")
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=content,
            media_type="image/jpeg",
            headers={"ETag": etag, "Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/reviews/{kind}/{period}/regenerate")
    def regenerate_review(
        kind: str,
        period: str,
        username: str = Depends(current_username),
    ) -> JSONResponse:
        del username
        valid_kind, valid_period = _validate_review_period(kind, period)
        if review_client is None:
            raise HTTPException(
                status_code=503,
                detail="review_regeneration_disabled",
            )
        try:
            status, body = review_client.regenerate(valid_kind, valid_period)
        except httpx.RequestError as error:
            raise HTTPException(status_code=502, detail="regeneration disabled") from error
        if status != 202 or not isinstance(body.get("job_id"), int):
            detail = body.get("detail")
            if status in {404, 409, 422}:
                raise HTTPException(
                    status_code=status,
                    detail=detail if isinstance(detail, str) else "regeneration failed",
                )
            raise HTTPException(status_code=502, detail="regeneration disabled")
        return JSONResponse(status_code=202, content=body)

    @app.get("/api/review-jobs/{job_id}")
    def review_job(
        job_id: int,
        username: str = Depends(current_username),
    ) -> JSONResponse:
        del username
        if review_client is None:
            raise HTTPException(
                status_code=503,
                detail="review_regeneration_disabled",
            )
        try:
            status, body = review_client.get_job(job_id)
        except httpx.RequestError as error:
            raise HTTPException(status_code=502, detail="regeneration disabled") from error
        if status == 200:
            return JSONResponse(content=body)
        if status == 404:
            raise HTTPException(status_code=404, detail="Review job not found")
        raise HTTPException(status_code=502, detail="regeneration disabled")

    return app


def _settings_from_env() -> WebSettings:
    return WebSettings.from_env()


def _calendar_day(day: DayRecord) -> dict[str, object]:
    return {
        "date": day.date,
        "weekday": day.weekday,
        "is_weekend": day.is_weekend,
        "season": day.season,
        "mood": day.mood,
        "mood_confidence": day.mood_confidence,
        "summary": day.summary,
        "facts": dict(day.facts),
    }


def _day_payload(
    *,
    vault_dir: Path,
    store: EnrichmentReadStore,
    target_date: str,
    dates: list[str],
) -> dict[str, object]:
    try:
        blocks = read_day(vault_dir=vault_dir, day=target_date)
    except DayNotFound as exc:
        raise HTTPException(status_code=404, detail="Day not found") from exc

    try:
        index = dates.index(target_date)
    except ValueError:
        index = -1
    day = store.get_day(target_date)
    return {
        "date": target_date,
        "prev_date": dates[index - 1] if index > 0 else None,
        "next_date": dates[index + 1] if 0 <= index < len(dates) - 1 else None,
        "day": None if day is None else _journal_day(day),
        "notes": _day_notes(blocks=blocks, store=store, target_date=target_date),
    }


def _day_notes(
    *,
    blocks: list[DayNoteBlock],
    store: EnrichmentReadStore,
    target_date: str,
) -> list[dict[str, object]]:
    notes: list[dict[str, object]] = []
    for identified in identify_day_note_blocks(blocks=blocks, target_date=target_date):
        block = identified.block
        note_id = identified.id
        record = store.get_note(note_id)
        notes.append(
            {
                "id": note_id,
                "ts": block.ts,
                "kind": block.kind,
                "heading_display": block.heading_display,
                "raw_text": block.raw_text,
                "raw_text_sha256": raw_text_sha256(block.raw_text),
                "mood": None if record is None else record.mood,
                "topics": [] if record is None else record.topics,
                "gist": None if record is None else record.gist,
            }
        )
    return notes


def _journal_day(day: DayRecord) -> dict[str, object]:
    return {
        "mood": day.mood,
        "mood_confidence": day.mood_confidence,
        "summary": day.summary,
        "key_topics": day.key_topics,
        "weekday": day.weekday,
        "is_weekend": day.is_weekend,
        "season": day.season,
        "facts": dict(day.facts),
    }


def _validate_month(month: str) -> str:
    if not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(status_code=422, detail="Invalid month")
    try:
        date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid month") from exc
    return month


def _validate_new_text(new_text: str) -> None:
    trimmed = new_text.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="text must not be empty")
    if len(trimmed) > 50_000:
        raise HTTPException(status_code=422, detail="text is too long")
    if ENRICHMENT_MARKER in trimmed:
        raise HTTPException(
            status_code=422,
            detail="text must not contain managed enrichment markers",
        )
    for line in trimmed.splitlines():
        if line.startswith("## ") or ENTRY_HEADING_RE.match(line.strip()):
            raise HTTPException(
                status_code=422,
                detail="text must not contain note headings (## HH:MM)",
            )


def _week_period(raw_date: str) -> str:
    iso = date.fromisoformat(raw_date).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _validate_review_kind(kind: str) -> str:
    if kind not in {"week", "month"}:
        raise HTTPException(status_code=422, detail="Invalid review kind")
    return kind


def _validate_review_period(kind: str, period: str) -> tuple[str, str]:
    valid_kind = _validate_review_kind(kind)
    try:
        if valid_kind == "week":
            value = date.fromisoformat(period)
            if value.weekday() != 6:
                raise ValueError("Weekly period must start on Sunday")
        else:
            if not re.fullmatch(r"\d{4}-\d{2}", period):
                raise ValueError("Invalid monthly period")
            date.fromisoformat(f"{period}-01")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid review period") from error
    return valid_kind, period


def _review_archive_item(record: dict[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    counts = payload.get("counts", {}) if isinstance(payload, dict) else {}
    return {
        "kind": record["kind"],
        "period": record["period"],
        "status": record["status"],
        "start_date": record["start_date"],
        "end_date": record["end_date"],
        "title": record["title"],
        "counts": counts if isinstance(counts, dict) else {},
        "has_image": bool(record.get("image_path")),
        "language": record["language"],
        "version": record.get("version", 1),
        "updated_at": record.get("updated_at"),
    }


def _review_detail_payload(
    record: dict[str, object], sources: list[dict[str, object]]
) -> dict[str, object]:
    source_by_id = {
        str(source["source_id"]): source
        for source in sources
        if source.get("source_type") != "external"
    }
    raw_payload = record.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_paragraphs = payload.get("paragraphs", [])
    paragraphs: list[dict[str, object]] = []
    if isinstance(raw_paragraphs, list):
        for raw in raw_paragraphs:
            if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                continue
            evidence: list[dict[str, object]] = []
            refs = raw.get("evidence_refs", [])
            if isinstance(refs, list):
                for reference in refs:
                    source = source_by_id.get(str(reference))
                    if source is None:
                        continue
                    public = _public_evidence(source)
                    if public is not None:
                        evidence.append(public)
            paragraphs.append({"text": raw["text"], "evidence": evidence})

    image = None
    if record.get("image_path"):
        image = {
            "url": f"/api/reviews/{record['kind']}/{record['period']}/image",
            "alt": record.get("image_alt") or "Generated review poster",
        }
    counts = payload.get("counts", {})
    return {
        "kind": record["kind"],
        "period": record["period"],
        "status": record["status"],
        "start_date": record["start_date"],
        "end_date": record["end_date"],
        "title": record["title"],
        "paragraphs": paragraphs,
        "reflection_question": record["reflection_question"],
        "safety_note": record.get("safety_note"),
        "counts": counts if isinstance(counts, dict) else {},
        "image": image,
        "language": record["language"],
        "model": record["model"],
        "version": record.get("version", 1),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _public_evidence(source: dict[str, object]) -> dict[str, object] | None:
    source_id = str(source["source_id"])
    source_type = str(source["source_type"])
    if source_type == "diary":
        match = re.match(r"^diary:(\d{4}-\d{2}-\d{2})T", source_id)
        if match is None:
            return None
        label = str(source.get("label") or match.group(1))
        return {
            "id": source_id,
            "type": "diary",
            "label": label,
            "href": f"#journal/{match.group(1)}",
        }
    if source_type == "review":
        match = re.match(r"^review:(week|month):([^:]+)$", source_id)
        if match is None:
            return None
        return {
            "id": source_id,
            "type": "review",
            "label": str(source.get("label") or match.group(2)),
            "href": f"#reviews/{match.group(1)}/{match.group(2)}",
        }
    if source_type == "vault" and source_id.startswith("vault:"):
        relative = source_id.removeprefix("vault:").split("#", 1)[0]
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            return None
        return {
            "id": source_id,
            "type": "vault",
            "label": path.as_posix(),
            "href": None,
        }
    return None


def _resolve_review_image(root: Path, stored: str, *, expected_stem: str) -> Path:
    resolved_root = root.resolve()
    stored_path = Path(stored)
    filename = stored_path.name
    legacy_name = f"{expected_stem}.jpg"
    job_prefix = f"{expected_stem}-job-"
    job_suffix = filename.removeprefix(job_prefix).removesuffix(".jpg")
    is_immutable_job_image = (
        filename.startswith(job_prefix)
        and filename.endswith(".jpg")
        and bool(job_suffix)
        and job_suffix.isdigit()
    )
    if filename != legacy_name and not is_immutable_job_image:
        raise HTTPException(status_code=404, detail="Review image not found")
    if stored_path.is_absolute():
        candidate = stored_path.resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            # Bot and web use different absolute mount points in Docker. The
            # deterministic filename is the cross-container identity.
            candidate = (resolved_root / filename).resolve()
    else:
        if stored_path != Path(filename):
            raise HTTPException(status_code=404, detail="Review image not found")
        candidate = (resolved_root / filename).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Review image not found") from error
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Review image not found")
    return candidate
