import asyncio
import re
from html import unescape
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from dairy_bot.handlers import journal
from dairy_bot.services.language_store import set_language
from dairy_bot.texts import messages


class FakeSettings:
    journal_dir = "/tmp/test-journal"
    timezone = ZoneInfo("Europe/Vienna")


class FakeGit:
    def sync_from_remote(self, *, allow_dirty=False):
        return True


class FakeState:
    def __init__(self):
        self.data = {}
        self.state = None

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)


class FakeBot:
    async def download(self, voice, destination):
        return None


class FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


class FakeMessage:
    def __init__(self, *, fail_on_calls=(), user_id=918273):
        self.from_user = SimpleNamespace(id=user_id)
        self.voice = SimpleNamespace(file_id="voice-file")
        self.bot = FakeBot()
        self.answers = []
        self.answer_calls = 0
        self.fail_on_calls = set(fail_on_calls)

    async def answer(self, text, **kwargs):
        self.answer_calls += 1
        if self.answer_calls in self.fail_on_calls:
            raise RuntimeError("simulated Telegram delivery failure")
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=self.answer_calls)


def run(coro):
    return asyncio.run(coro)


def prepare_voice(monkeypatch, transcription):
    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    async def fake_transcribe_audio(path, settings):
        return transcription

    monkeypatch.setattr(
        journal.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(journal, "transcribe_audio", fake_transcribe_audio)


def numbered_parts(message):
    return [text for text, _ in message.answers if re.search(r"\n\d+/\d+$", text)]


def voice_payload(parts):
    payload = []
    for part in parts:
        match = re.search(r"<blockquote>(.*?)</blockquote>", part, re.DOTALL)
        assert match is not None
        payload.append(unescape(match.group(1)))
    return "".join(payload)


def today_payload(parts, header):
    payload = []
    for index, part in enumerate(parts):
        without_number = re.sub(r"\n\d+/\d+$", "", part)
        if index == 0:
            assert without_number.startswith(f"{header}\n\n")
            without_number = without_number.removeprefix(f"{header}\n\n")
        payload.append(unescape(without_number))
    return "".join(payload)


def test_AC_1_AC_2_EC_3_voice_preview_keeps_single_message_at_limit_and_splits_above_it(
    monkeypatch,
):
    empty_preview = messages.format_transcription_preview("", "en")
    exact_transcription = "x" * (journal.MAX_TG_MESSAGE_LEN - len(empty_preview))
    prepare_voice(monkeypatch, exact_transcription)
    exact_message = FakeMessage()
    exact_state = FakeState()

    run(journal.handle_voice(exact_message, exact_state, FakeSettings()))

    assert len(exact_message.answers) == 1
    assert exact_message.answers[0][0] == messages.format_transcription_preview(
        exact_transcription, "en"
    )
    assert exact_message.answers[0][1]["reply_markup"] is not None
    assert not re.search(r"\n\d+/\d+$", exact_message.answers[0][0])

    over_transcription = exact_transcription + " y"
    prepare_voice(monkeypatch, over_transcription)
    over_message = FakeMessage()

    run(journal.handle_voice(over_message, FakeState(), FakeSettings()))

    assert len(over_message.answers) == 2
    assert all(len(text) <= journal.MAX_TG_MESSAGE_LEN for text, _ in over_message.answers)
    assert [text.rsplit("\n", 1)[-1] for text, _ in over_message.answers] == [
        "1/2",
        "2/2",
    ]


def test_AC_2_AC_3_AC_4_AC_7_long_voice_preview_is_complete_ordered_and_only_last_part_has_buttons(
    monkeypatch,
):
    transcription = " ".join(f"voice-{index:04d}" for index in range(1200))
    prepare_voice(monkeypatch, transcription)
    message = FakeMessage()
    state = FakeState()

    run(journal.handle_voice(message, state, FakeSettings()))

    parts = numbered_parts(message)
    assert len(parts) >= 3
    assert all(len(part) <= journal.MAX_TG_MESSAGE_LEN for part in parts)
    assert [part.rsplit("\n", 1)[-1] for part in parts] == [
        f"{index}/{len(parts)}" for index in range(1, len(parts) + 1)
    ]
    assert voice_payload(parts) == transcription
    assert all(kwargs.get("reply_markup") is None for _, kwargs in message.answers[:-1])
    assert message.answers[-1][1]["reply_markup"] is not None
    assert state.state == journal.VoiceStates.waiting_decision
    assert state.data == {"transcription": transcription}


def test_AC_5_AC_6_AC_7_today_keeps_short_reply_and_numbers_complete_long_reply(
    monkeypatch,
):
    short_content = "A short journal entry"

    async def short_note(*args, **kwargs):
        return short_content

    monkeypatch.setattr(journal, "read_daily_note_entries", short_note)
    short_message = FakeMessage()

    run(journal.handle_today(short_message, FakeSettings(), FakeGit()))

    assert len(short_message.answers) == 1
    assert short_message.answers[0][0].endswith(short_content)
    assert not re.search(r"\n\d+/\d+$", short_message.answers[0][0])

    long_content = " ".join(f"today-{index:04d}" for index in range(1200))

    async def long_note(*args, **kwargs):
        return long_content

    monkeypatch.setattr(journal, "read_daily_note_entries", long_note)
    long_message = FakeMessage()

    run(journal.handle_today(long_message, FakeSettings(), FakeGit()))

    parts = numbered_parts(long_message)
    assert parts == [text for text, _ in long_message.answers]
    assert len(parts) >= 3
    assert all(len(part) <= journal.MAX_TG_MESSAGE_LEN for part in parts)
    header = parts[0].split("\n", 1)[0]
    assert today_payload(parts, header) == long_content


def test_EC_1_EC_3_split_boundaries_use_whitespace_and_account_for_escaped_html(
    monkeypatch,
):
    transcription = " ".join(
        f"html-word-{index:04d}<&>" for index in range(800)
    )
    prepare_voice(monkeypatch, transcription)
    message = FakeMessage()

    run(journal.handle_voice(message, FakeState(), FakeSettings()))

    parts = numbered_parts(message)
    bodies = [
        unescape(re.search(r"<blockquote>(.*?)</blockquote>", part, re.DOTALL).group(1))
        for part in parts
    ]
    assert "".join(bodies) == transcription
    assert all(
        left[-1].isspace() or right[0].isspace()
        for left, right in zip(bodies, bodies[1:])
    )
    assert all(len(part) <= journal.MAX_TG_MESSAGE_LEN for part in parts)
    assert all("&lt;&amp;&gt;" in part for part in parts)


def test_EC_2_oversized_word_is_hard_split_without_losing_characters(monkeypatch):
    transcription = "Ж" * (journal.MAX_TG_MESSAGE_LEN * 3)
    prepare_voice(monkeypatch, transcription)
    message = FakeMessage()

    run(journal.handle_voice(message, FakeState(), FakeSettings()))

    parts = numbered_parts(message)
    assert len(parts) >= 4
    assert all(len(part) <= journal.MAX_TG_MESSAGE_LEN for part in parts)
    assert voice_payload(parts) == transcription


def test_ERR_1_failed_voice_part_stops_delivery_and_does_not_enter_confirmation_state(
    monkeypatch,
):
    transcription = " ".join(f"failure-{index:04d}" for index in range(1600))
    prepare_voice(monkeypatch, transcription)
    message = FakeMessage(fail_on_calls={2})
    state = FakeState()

    run(journal.handle_voice(message, state, FakeSettings()))

    assert message.answer_calls == 3
    assert message.answers[-1][0] == (
        "⚠️ I couldn't send the complete message. Please try again."
    )
    assert message.answers[-1][1].get("reply_markup") is None
    assert state.state is None
    assert state.data == {}


def test_ERR_1_failed_today_part_stops_remaining_parts_and_reports_localized_error(
    monkeypatch,
):
    content = " ".join(f"ошибка-{index:04d}" for index in range(1600))

    async def long_note(*args, **kwargs):
        return content

    monkeypatch.setattr(journal, "read_daily_note_entries", long_note)
    russian_user_id = 918274
    set_language(russian_user_id, "ru")
    message = FakeMessage(fail_on_calls={2}, user_id=russian_user_id)

    run(journal.handle_today(message, FakeSettings(), FakeGit()))

    assert message.answer_calls == 3
    assert message.answers[-1][0] == (
        "⚠️ Не удалось отправить сообщение полностью. Попробуйте ещё раз."
    )
    assert message.answers[-1][1].get("reply_markup") is None
