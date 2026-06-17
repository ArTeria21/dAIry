import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramNetworkError

from dairy_bot.handlers import journal
from dairy_bot.services import storage
from dairy_bot.services.enrichment_schemas import Mood, NoteEnrichment, Topic
from dairy_bot.services.git_sync import GitSyncError
from dairy_bot.texts import LANG_RU


TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 6, 16, 21, 55, tzinfo=TZ)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return NOW.replace(tzinfo=None)
        return NOW.astimezone(tz)


class FakeSettings:
    def __init__(self, journal_dir: Path, *, enrichment_enabled: bool = True):
        self.journal_dir = journal_dir
        self.timezone = TZ
        self.toc_enabled = False
        self.toc_filename = "table_of_contents.md"
        self.toc_extra_dirs = []
        self.toc_model = "test/model"
        self.toc_max_tags = 5
        self.enrichment_enabled = enrichment_enabled
        self.enrichment_db_path = journal_dir / "data" / "enrichment.sqlite3"


class FakeGit:
    def __init__(self, *, block_prepare: bool = False):
        self.block_prepare = block_prepare
        self.committed_paths: list[Path] | None = None

    def prepare_for_write(self):
        if self.block_prepare:
            raise GitSyncError("blocked")

    def sync_from_remote(self, *, allow_dirty: bool = False):
        return True

    def commit_and_push(self, paths):
        self.committed_paths = list(paths)
        return SimpleNamespace(pushed=True)


class FakeState:
    def __init__(self):
        self.data = {}
        self.state = None

    async def clear(self):
        self.data.clear()
        self.state = None

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.answers: list[FakeSentMessage] = []
        self.from_user = SimpleNamespace(id=123)

    async def answer(self, text, **kwargs):
        sent = FakeSentMessage(text)
        self.answers.append(sent)
        return sent

    async def edit_reply_markup(self, **kwargs):
        return self


class ProgressFailingMessage(FakeMessage):
    def __init__(self, text: str):
        super().__init__(text)
        self.progress_failures_remaining = 1

    async def answer(self, text, **kwargs):
        if self.progress_failures_remaining:
            self.progress_failures_remaining -= 1
            raise TelegramNetworkError(method=None, message="temporary outage")
        return await super().answer(text, **kwargs)


class FakeSentMessage:
    def __init__(self, text: str):
        self.text = text
        self.edits: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.edits.append(text)
        return self


class FakeCallback:
    def __init__(self):
        self.answers: list[str] = []
        self.message = FakeMessage("")
        self.from_user = SimpleNamespace(id=123)

    async def answer(self, text=None, **kwargs):
        self.answers.append(text or "")


class FakeNoteClient:
    def __init__(self):
        self.note_calls: list[str] = []

    async def enrich_note(self, text: str) -> NoteEnrichment:
        self.note_calls.append(text)
        return NoteEnrichment(
            gist="The user wrote a current-day note.",
            mood_evidence="The note sounds calm.",
            mood=Mood.calm,
            mood_confidence=0.77,
            topics=[Topic.reflection, Topic.productivity],
        )

    async def embed_note(self, text: str) -> list[float]:
        return [0.4, 0.5]


class FailingDayClient:
    def __init__(self):
        self.day_calls: list[str] = []
        self.closed = False

    async def enrich_day(self, text: str):
        self.day_calls.append(text)
        raise RuntimeError("day enrichment failed")

    async def close(self):
        self.closed = True


def run(coro):
    return asyncio.run(coro)


def note_path(root: Path, day: str) -> Path:
    year, month, _ = day.split("-")
    return root / year / month / f"{day}.md"


def freeze_handlers(monkeypatch):
    monkeypatch.setattr(journal, "datetime", FrozenDateTime)
    monkeypatch.setattr(storage, "datetime", FrozenDateTime)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_AC_1_today_text_save_edits_one_status_message_saved_enriched_synced(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    client = FakeNoteClient()
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    message = FakeMessage("Сегодняшняя спокойная заметка")

    run(journal.handle_text(message, FakeState(), settings, git))

    today_path = note_path(tmp_path, "2026-06-16")
    assert len(message.answers) == 1
    status = message.answers[0]
    assert status.edits == [
        "✅ Note written to file\n⏳ Processing note with LLM...\n⏳ Syncing with git...",
        "✅ Note written to file\n✅ LLM processed note. Mood: calm (0.77), topics: reflection, productivity\n⏳ Syncing with git...",
        "✅ Note written to file\n✅ LLM processed note. Mood: calm (0.77), topics: reflection, productivity\n✅ Synced with git",
    ]
    assert "mood:: calm · topics:: reflection, productivity" in read_text(today_path)
    assert client.note_calls == ["Сегодняшняя спокойная заметка"]
    assert git.committed_paths and today_path in git.committed_paths


def test_AC_1_today_text_save_uses_selected_russian_for_progress_status(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    client = FakeNoteClient()
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)
    monkeypatch.setattr(journal, "get_language", lambda user_id: LANG_RU)
    settings = FakeSettings(tmp_path)
    message = FakeMessage("Сегодняшняя спокойная заметка")

    run(journal.handle_text(message, FakeState(), settings, FakeGit()))

    assert message.answers[0].text == (
        "✅ Заметка записана в файл\n"
        "✅ Заметка обработана LLM. Настроение: спокойствие (0.77), "
        "темы: рефлексия, продуктивность\n"
        "✅ Синхронизировано с git"
    )
    assert "Note written to file" not in message.answers[0].text


def test_AC_2_post_factum_save_does_not_call_immediate_note_llm_or_show_enrichment_progress(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    client = FakeNoteClient()
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)
    settings = FakeSettings(tmp_path)
    state = FakeState()

    run(journal._set_entry_target_date(state, date(2026, 6, 13)))
    message = FakeMessage("Post-factum note")
    run(journal.handle_text(message, state, settings, FakeGit()))

    target_path = note_path(tmp_path, "2026-06-13")
    assert len(message.answers) == 1
    assert message.answers[0].edits == ["✅ Saved and synced."]
    assert "Post-factum note" in read_text(target_path)
    assert "mood::" not in read_text(target_path)
    assert client.note_calls == []


def test_ERR_1_today_save_keeps_raw_note_when_note_level_enrichment_fails(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)

    class FailingClient(FakeNoteClient):
        async def enrich_note(self, text: str) -> NoteEnrichment:
            raise RuntimeError("boom")

    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: FailingClient())
    settings = FakeSettings(tmp_path)
    message = FakeMessage("Сегодняшняя заметка с ошибкой enrichment")

    run(journal.handle_text(message, FakeState(), settings, FakeGit()))

    content = read_text(note_path(tmp_path, "2026-06-16"))
    assert "Сегодняшняя заметка с ошибкой enrichment" in content
    assert "mood::" not in content
    assert message.answers[0].edits[-1] == (
        "✅ Note written to file\n"
        "⚠️ LLM processing failed; I will retry in the background\n"
        "✅ Synced with git"
    )


def test_ERR_2_blocked_repo_edits_existing_progress_message(tmp_path, monkeypatch):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    git = FakeGit(block_prepare=True)
    message = FakeMessage("This should not be written")

    run(journal.handle_text(message, FakeState(), settings, git))

    today_path = note_path(tmp_path, "2026-06-16")
    assert not today_path.exists()
    assert git.committed_paths is None
    assert len(message.answers) == 1
    assert message.answers[0].text == (
        "⚠️ I couldn't sync the journal repo safely. Commit, stash, or "
        "revert local changes first, then try again."
    )
    assert message.answers[0].edits == [
        "⚠️ I couldn't sync the journal repo safely. Commit, stash, or "
        "revert local changes first, then try again."
    ]


def test_ERR_3_enrich_reports_failure_when_day_level_llm_fails(tmp_path, monkeypatch):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    client = FailingDayClient()
    git = FakeGit()
    message = FakeMessage("/enrich")
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)

    run(
        storage.append_entry(
            tmp_path,
            "A note that needs day enrichment",
            moment=NOW,
            timezone=TZ,
        )
    )

    run(journal.handle_enrich(message, settings, git))

    assert client.day_calls
    assert client.closed is True
    assert git.committed_paths is None
    assert len(message.answers) == 1
    assert message.answers[0].text == (
        "⚠️ Enrichment failed. I will retry during the next background run."
    )


def test_ERR_4_text_progress_message_failure_does_not_block_markdown_save(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    client = FakeNoteClient()
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    message = ProgressFailingMessage("Text survives progress outage")

    run(journal.handle_text(message, FakeState(), settings, git))

    today_path = note_path(tmp_path, "2026-06-16")
    assert "Text survives progress outage" in read_text(today_path)
    assert git.committed_paths and today_path in git.committed_paths
    assert client.note_calls == ["Text survives progress outage"]
    assert message.progress_failures_remaining == 0
    assert len(message.answers) == 1
    assert message.answers[0].edits == ["✅ Saved and synced."]


def test_ERR_5_edit_progress_message_failure_does_not_block_markdown_save(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    client = FakeNoteClient()
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()
    message = ProgressFailingMessage("Edited text survives progress outage")

    run(journal.handle_edit(message, state, settings, git))

    today_path = note_path(tmp_path, "2026-06-16")
    assert "Edited text survives progress outage" in read_text(today_path)
    assert git.committed_paths and today_path in git.committed_paths
    assert client.note_calls == ["Edited text survives progress outage"]
    assert state.data == {}
    assert message.progress_failures_remaining == 0
    assert len(message.answers) == 1
    assert message.answers[0].text == "✅ Saved and synced."


def test_ERR_6_voice_confirm_progress_message_failure_does_not_block_markdown_save(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    client = FakeNoteClient()
    monkeypatch.setattr(journal, "build_enrichment_client", lambda settings: client)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()
    callback = FakeCallback()
    callback.message = ProgressFailingMessage("")

    run(state.update_data(transcription="Voice text survives progress outage"))
    run(journal.confirm_voice(callback, state, settings, git))

    today_path = note_path(tmp_path, "2026-06-16")
    assert "Voice text survives progress outage" in read_text(today_path)
    assert git.committed_paths and today_path in git.committed_paths
    assert client.note_calls == ["Voice text survives progress outage"]
    assert callback.answers == ["Saving..."]
    assert state.data == {}
    assert callback.message.progress_failures_remaining == 0
    assert len(callback.message.answers) == 1
    assert callback.message.answers[0].text == "✅ Saved and synced."


def test_ERR_7_voice_confirm_acknowledges_callback_before_long_save(monkeypatch, tmp_path):
    settings = FakeSettings(tmp_path, enrichment_enabled=False)
    state = FakeState()
    callback = FakeCallback()

    run(state.update_data(transcription="Voice callback timing"))

    async def fake_save_entry_with_sync(*args, **kwargs):
        assert callback.answers == ["Saving..."]
        return "synced"

    monkeypatch.setattr(journal, "_save_entry_with_sync", fake_save_entry_with_sync)

    run(journal.confirm_voice(callback, state, settings, FakeGit()))

    assert callback.answers == ["Saving..."]
    assert state.data == {}
