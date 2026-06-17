from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel

from dairy_web.analysis import AnalysisCache, AnalysisService, OpenRouterClusterLabeler
from dairy_web.auth import (
    Argon2PasswordVerifier,
    AuthService,
    InvalidCredentials,
    RateLimitExceeded,
)
from dairy_web.data_access import DayRecord, EnrichmentReadStore, NoteRecord
from dairy_web.resurface import choose_resurface_day
from dairy_web.settings import WebSettings
from dairy_web.vault_reader import NoteRawTextNotFound, extract_note_raw_text


SESSION_COOKIE = "dairy_session"


class LoginRequest(BaseModel):
    username: str
    password: str


def create_app(
    *,
    settings: WebSettings | None = None,
    store: EnrichmentReadStore | None = None,
    analysis: AnalysisService | None = None,
    auth: AuthService | None = None,
    vault_dir: Path | str | None = None,
    cookie_secure: bool | None = None,
) -> FastAPI:
    if settings is None and (
        store is None or analysis is None or auth is None or vault_dir is None
    ):
        settings = _settings_from_env()

    store = store or EnrichmentReadStore(settings.enrichment_db_path)  # type: ignore[union-attr]
    analysis = analysis or AnalysisService(
        store=store,
        cache=AnalysisCache(settings.analysis_cache_path),  # type: ignore[union-attr]
        labeler=OpenRouterClusterLabeler(
            model_name=settings.enrichment_model_name,  # type: ignore[union-attr]
            api_key=settings.openrouter_api_key,  # type: ignore[union-attr]
            base_url=settings.openrouter_base_url,  # type: ignore[union-attr]
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

    app = FastAPI(title="dAIry Analytics API")

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
        }

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
            raw_text = extract_note_raw_text(
                vault_dir=vault_root,
                note_path=note.note_path,
                ts=note.ts,
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
            "day_summary": None if day is None else day.summary,
            "note_path": note.note_path,
        }

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
        for note in store.list_notes():
            period = _week_period(note.date)
            counts = counts_by_period.setdefault(period, defaultdict(int))
            for topic in note.topics:
                counts[topic] += 1
        return {
            "buckets": [
                {"period": period, "counts": dict(counts)}
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


def _week_period(raw_date: str) -> str:
    iso = date.fromisoformat(raw_date).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
