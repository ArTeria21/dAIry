from __future__ import annotations

import asyncio
import hmac
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from dairy_bot.config import Settings
from dairy_bot.services.git_sync import GitPushError, GitService, GitSyncError
from dairy_bot.services.journal_lock import get_journal_lock
from dairy_bot.services.note_editing import (
    NoteEditConflict,
    NoteEditNotFound,
    NoteEditValidationError,
    replace_note_text,
)

logger = logging.getLogger(__name__)
SETTINGS_KEY = web.AppKey("settings", Settings)
GIT_SERVICE_KEY = web.AppKey("git_service", GitService)


def create_edit_app(settings: Settings, git_service: GitService) -> web.Application:
    app = web.Application()
    app[SETTINGS_KEY] = settings
    app[GIT_SERVICE_KEY] = git_service
    app.router.add_post("/internal/notes/replace-text", _replace_text)
    return app


async def start_edit_api(
    settings: Settings,
    git_service: GitService,
) -> web.AppRunner | None:
    token = _edit_token(settings)
    if not token:
        logger.info("Edit API disabled because EDIT_API_TOKEN is not set")
        return None

    runner = web.AppRunner(create_edit_app(settings, git_service))
    await runner.setup()
    site = web.TCPSite(runner, settings.edit_api_host, settings.edit_api_port)
    await site.start()
    logger.info("Edit API listening on %s:%s", settings.edit_api_host, settings.edit_api_port)
    return runner


async def stop_edit_api(runner: web.AppRunner | None) -> None:
    if runner is not None:
        await runner.cleanup()


async def _replace_text(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    git_service = request.app[GIT_SERVICE_KEY]
    token = _edit_token(settings)
    if not token or not _authorized(request, token):
        return web.json_response({"detail": "Unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"detail": "Invalid JSON"}, status=422)

    try:
        result = await replace_text_with_sync(settings, git_service, payload)
    except NoteEditValidationError as exc:
        return web.json_response({"detail": str(exc)}, status=422)
    except NoteEditConflict:
        return web.json_response({"detail": "note changed elsewhere"}, status=409)
    except NoteEditNotFound:
        return web.json_response({"detail": "note not found"}, status=404)
    except FileNotFoundError:
        return web.json_response({"detail": "note not found"}, status=404)
    except PermissionError:
        logger.warning("Rejected edit outside journal root")
        return web.json_response({"detail": "note not found"}, status=404)
    except GitSyncError:
        logger.warning("Edit failed during git synchronization", exc_info=True)
        return web.json_response({"detail": "git sync failed"}, status=500)
    except Exception:
        logger.exception("Unexpected edit API failure")
        return web.json_response({"detail": "edit failed"}, status=500)

    return web.json_response({"new_sha256": result})


async def replace_text_with_sync(
    settings: Settings,
    git_service: GitService,
    payload: dict[str, Any],
) -> str:
    note_id = _required_str(payload, "note_id")
    note_path = _required_str(payload, "note_path")
    expected_sha256 = _required_str(payload, "expected_sha256")
    new_text = _required_str(payload, "new_text")
    full_path = _resolve_note_path(settings.journal_dir, note_path)

    async with get_journal_lock():
        await asyncio.to_thread(git_service.prepare_for_write)
        content = await asyncio.to_thread(full_path.read_text, encoding="utf-8")
        replacement = replace_note_text(
            content=content,
            note_id=note_id,
            note_path=note_path,
            expected_sha256=expected_sha256,
            new_text=new_text,
        )
        await asyncio.to_thread(full_path.write_text, replacement.content, encoding="utf-8")
        try:
            await asyncio.to_thread(git_service.commit_and_push, [full_path])
        except GitPushError:
            logger.warning("Edit saved and committed locally, but push failed", exc_info=True)
        return replacement.new_sha256


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise NoteEditValidationError(f"{key} is required")
    return value


def _resolve_note_path(journal_dir: Path, note_path: str) -> Path:
    root = Path(journal_dir).resolve()
    full_path = (root / note_path).resolve()
    try:
        full_path.relative_to(root)
    except ValueError as exc:
        raise PermissionError("note path outside journal") from exc
    if not full_path.is_file():
        raise FileNotFoundError(note_path)
    return full_path


def _authorized(request: web.Request, token: str) -> bool:
    provided = request.headers.get("X-Edit-Token", "")
    return hmac.compare_digest(provided, token)


def _edit_token(settings: Settings) -> str:
    if settings.edit_api_token is None:
        return ""
    return settings.edit_api_token.get_secret_value()
