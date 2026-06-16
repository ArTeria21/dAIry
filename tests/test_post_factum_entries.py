import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.handlers import journal
from dairy_bot.services import storage
from dairy_bot.services.git_sync import GitSyncError


TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 6, 16, 21, 55, tzinfo=TZ)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return NOW.replace(tzinfo=None)
        return NOW.astimezone(tz)


class FakeSettings:
    def __init__(self, journal_dir: Path):
        self.journal_dir = journal_dir
        self.timezone = TZ
        self.toc_enabled = False
        self.toc_filename = "table_of_contents.md"
        self.toc_extra_dirs = []
        self.toc_model = "test/model"
        self.toc_max_tags = 5


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
        self.answers: list[str] = []
        self.from_user = SimpleNamespace(id=123)

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return SimpleNamespace()


class FakeCallback:
    def __init__(self):
        self.answers: list[str] = []
        self.message = FakeMessage("")
        self.from_user = SimpleNamespace(id=123)

    async def answer(self, text=None, **kwargs):
        self.answers.append(text or "")


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


def test_AC_1_yesterday_sets_one_shot_target_and_saves_next_text_to_yesterday(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()

    command = FakeMessage("/yesterday")
    run(journal.handle_yesterday(command, state, settings))

    assert command.answers == [
        "Next entry will be saved to 15-06-2026. If the note does not exist, I will create it."
    ]

    entry = FakeMessage("A note from yesterday")
    run(journal.handle_text(entry, state, settings, git))

    yesterday_path = note_path(tmp_path, "2026-06-15")
    today_path = note_path(tmp_path, "2026-06-16")
    assert yesterday_path.exists()
    assert "A note from yesterday" in read_text(yesterday_path)
    assert not today_path.exists()


def test_AC_2_EC_1_EC_2_day_command_sets_valid_target_and_rejects_invalid_dates_without_replacing_existing_target(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()

    valid = FakeMessage("/day 13-06-2026")
    run(journal.handle_day(valid, state, settings))
    assert valid.answers == [
        "Next entry will be saved to 13-06-2026. If the note does not exist, I will create it."
    ]

    invalid_format = FakeMessage("/day 2026-06-13")
    run(journal.handle_day(invalid_format, state, settings))
    assert invalid_format.answers == ["Use /day dd-mm-yyyy, for example /day 13-06-2026."]

    invalid_calendar_day = FakeMessage("/day 31-02-2026")
    run(journal.handle_day(invalid_calendar_day, state, settings))
    assert invalid_calendar_day.answers == ["Use /day dd-mm-yyyy, for example /day 13-06-2026."]

    entry = FakeMessage("Still goes to the valid target")
    run(journal.handle_text(entry, state, settings, git))

    target_path = note_path(tmp_path, "2026-06-13")
    today_path = note_path(tmp_path, "2026-06-16")
    assert target_path.exists()
    assert "Still goes to the valid target" in read_text(target_path)
    assert not today_path.exists()


def test_today_message_uses_day_month_year_display_date(tmp_path, monkeypatch):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)

    run(
        storage.append_entry(
            tmp_path,
            "Today display entry",
            moment=NOW,
            timezone=TZ,
        )
    )

    message = FakeMessage("/today")
    run(journal.handle_today(message, settings, FakeGit()))

    assert message.answers == [
        "📓 Today's note (16-06-2026)\n\n## 21:55\n\nToday display entry"
    ]


def test_day_command_rejects_future_dates_without_replacing_existing_target(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()

    run(journal.handle_day(FakeMessage("/day 13-06-2026"), state, settings))

    future_date = FakeMessage("/day 20-06-2026")
    run(journal.handle_day(future_date, state, settings))
    assert future_date.answers == ["Future dates are not available. Choose today or a past date."]

    entry = FakeMessage("Still goes to the earlier valid target")
    run(journal.handle_text(entry, state, settings, git))

    target_path = note_path(tmp_path, "2026-06-13")
    future_path = note_path(tmp_path, "2026-06-20")
    assert target_path.exists()
    assert "Still goes to the earlier valid target" in read_text(target_path)
    assert not future_path.exists()


def test_AC_3_back_clears_pending_target_before_next_text_save(tmp_path, monkeypatch):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()

    run(journal.handle_day(FakeMessage("/day 13-06-2026"), state, settings))
    back = FakeMessage("/back")
    run(journal.handle_back(back, state))

    assert back.answers == ["Date override cancelled. The next entry will be saved to today."]

    entry = FakeMessage("Current day after back")
    run(journal.handle_text(entry, state, settings, git))

    target_path = note_path(tmp_path, "2026-06-13")
    today_path = note_path(tmp_path, "2026-06-16")
    assert not target_path.exists()
    assert today_path.exists()
    assert "Current day after back" in read_text(today_path)


def test_AC_4_EC_3_text_save_uses_pending_target_once_and_creates_missing_daily_note(
    tmp_path, monkeypatch
):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    state = FakeState()

    run(journal.handle_day(FakeMessage("/day 13-06-2026"), state, settings))
    first = FakeMessage("First post-factum text")
    run(journal.handle_text(first, state, settings, git))
    second = FakeMessage("Second current-day text")
    run(journal.handle_text(second, state, settings, git))

    target_path = note_path(tmp_path, "2026-06-13")
    today_path = note_path(tmp_path, "2026-06-16")
    assert target_path.exists()
    assert today_path.exists()

    target_content = read_text(target_path)
    today_content = read_text(today_path)
    assert "date: 2026-06-13" in target_content
    assert "# 2026-06-13" in target_content
    assert "First post-factum text" in target_content
    assert "Second current-day text" not in target_content
    assert "Second current-day text" in today_content


def test_AC_5_voice_confirm_and_edit_use_pending_target_once(tmp_path, monkeypatch):
    freeze_handlers(monkeypatch)
    settings = FakeSettings(tmp_path)

    confirm_state = FakeState()
    run(journal.handle_day(FakeMessage("/day 13-06-2026"), confirm_state, settings))
    run(confirm_state.update_data(transcription="Confirmed voice text"))
    run(journal.confirm_voice(FakeCallback(), confirm_state, settings, FakeGit()))

    confirm_target = note_path(tmp_path, "2026-06-13")
    assert "Confirmed voice text" in read_text(confirm_target)
    assert confirm_state.data == {}

    edit_state = FakeState()
    run(journal.handle_day(FakeMessage("/day 12-06-2026"), edit_state, settings))
    edited = FakeMessage("Edited voice text")
    run(journal.handle_edit(edited, edit_state, settings, FakeGit()))

    edit_target = note_path(tmp_path, "2026-06-12")
    assert "Edited voice text" in read_text(edit_target)
    assert edit_state.data == {}


def test_AC_6_append_entry_formats_cross_day_and_same_day_headings(tmp_path):
    past_path = run(
        storage.append_entry(
            tmp_path,
            "Past entry",
            moment=NOW,
            timezone=TZ,
            target_date=date(2026, 6, 13),
        )
    )
    today_path = run(
        storage.append_entry(
            tmp_path,
            "Today entry",
            moment=NOW,
            timezone=TZ,
        )
    )

    assert past_path == note_path(tmp_path, "2026-06-13")
    assert "## June 16 21:55\n\nPast entry" in read_text(past_path)
    assert today_path == note_path(tmp_path, "2026-06-16")
    assert "## 21:55\n\nToday entry" in read_text(today_path)


def test_AC_7_ERR_1_save_pipeline_reconciles_target_note_and_blocks_without_write_when_git_prepare_fails(
    tmp_path, monkeypatch
):
    settings = FakeSettings(tmp_path)
    toc_path = tmp_path / "table_of_contents.md"
    state_path = tmp_path / ".toc_index.json"
    captured_target_paths: list[Path] = []

    async def fake_reconcile_toc(journal_dir, settings, target_paths=None):
        captured_target_paths.extend(target_paths or [])
        return [toc_path, state_path]

    monkeypatch.setattr(journal, "reconcile_toc", fake_reconcile_toc)

    git = FakeGit()
    status = run(
        journal._save_entry_with_sync(
            "Targeted pipeline",
            settings,
            git,
            target_date=date(2026, 6, 13),
            moment=NOW,
        )
    )

    target_path = note_path(tmp_path, "2026-06-13")
    assert status == "synced"
    assert captured_target_paths == [target_path]
    assert git.committed_paths == [target_path, toc_path, state_path]

    blocked_dir = tmp_path / "blocked"
    blocked_settings = FakeSettings(blocked_dir)
    blocked_status = run(
        journal._save_entry_with_sync(
            "Should not be written",
            blocked_settings,
            FakeGit(block_prepare=True),
            target_date=date(2026, 6, 13),
            moment=NOW,
        )
    )

    assert blocked_status == "blocked"
    assert not note_path(blocked_dir, "2026-06-13").exists()


def test_NAV_AC_1_AC_2_AC_3_EC_1_ERR_1_post_factum_save_refreshes_real_neighbor_chain(
    tmp_path,
):
    run(
        storage.append_entry(
            tmp_path,
            "March 1 entry",
            moment=datetime(2026, 3, 1, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    run(
        storage.append_entry(
            tmp_path,
            "March 3 first entry",
            moment=datetime(2026, 3, 3, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    run(
        storage.append_entry(
            tmp_path,
            "March 10 entry",
            moment=datetime(2026, 3, 10, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )

    march_1 = note_path(tmp_path, "2026-03-01")
    march_3 = note_path(tmp_path, "2026-03-03")
    march_10 = note_path(tmp_path, "2026-03-10")

    march_1.write_text(
        read_text(march_1).replace(
            "[[2026-03-03|Next day]]", "[[2026-03-10|Next day]]"
        ),
        encoding="utf-8",
    )
    march_3.write_text(
        read_text(march_3).replace(
            "[[2026-03-01|Prev day]] · [[2026-03-10|Next day]]",
            "[[2026-02-28|Prev day]] · [[2026-03-10|Next day]]",
        ),
        encoding="utf-8",
    )
    march_10.write_text(
        read_text(march_10).replace(
            "[[2026-03-03|Prev day]]", "[[2026-03-01|Prev day]]"
        ),
        encoding="utf-8",
    )

    empty_template = note_path(tmp_path, "2026-03-02")
    empty_template.parent.mkdir(parents=True, exist_ok=True)
    empty_template.write_text(
        "---\ndate: 2026-03-02\ntype: daily\n---\n# 2026-03-02\n\n\n",
        encoding="utf-8",
    )

    run(
        storage.append_entry(
            tmp_path,
            "Post-factum addition to existing March 3",
            moment=NOW,
            timezone=TZ,
            target_date=date(2026, 3, 3),
        )
    )

    assert "# 2026-03-01\n[[2026-03-03|Next day]]\n" in read_text(march_1)
    assert (
        "# 2026-03-03\n[[2026-03-01|Prev day]] · [[2026-03-10|Next day]]\n"
        in read_text(march_3)
    )
    assert "# 2026-03-10\n[[2026-03-03|Prev day]]\n" in read_text(march_10)


def test_NAV_inserting_past_note_between_existing_days_refreshes_adjacent_links(
    tmp_path,
):
    run(
        storage.append_entry(
            tmp_path,
            "March 1 entry",
            moment=datetime(2026, 3, 1, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    run(
        storage.append_entry(
            tmp_path,
            "March 5 entry",
            moment=datetime(2026, 3, 5, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    run(
        storage.append_entry(
            tmp_path,
            "March 15 entry",
            moment=datetime(2026, 3, 15, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )

    march_1 = note_path(tmp_path, "2026-03-01")
    march_3 = note_path(tmp_path, "2026-03-03")
    march_5 = note_path(tmp_path, "2026-03-05")
    march_15 = note_path(tmp_path, "2026-03-15")

    assert "# 2026-03-05\n[[2026-03-01|Prev day]] · [[2026-03-15|Next day]]\n" in read_text(
        march_5
    )

    run(
        storage.append_entry(
            tmp_path,
            "Inserted March 3 entry",
            moment=NOW,
            timezone=TZ,
            target_date=date(2026, 3, 3),
        )
    )

    assert "# 2026-03-01\n[[2026-03-03|Next day]]\n" in read_text(march_1)
    assert (
        "# 2026-03-03\n[[2026-03-01|Prev day]] · [[2026-03-05|Next day]]\n"
        in read_text(march_3)
    )
    assert (
        "# 2026-03-05\n[[2026-03-03|Prev day]] · [[2026-03-15|Next day]]\n"
        in read_text(march_5)
    )
    assert "# 2026-03-15\n[[2026-03-05|Prev day]]\n" in read_text(march_15)
