from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from dairy_bot.services.edit_api import create_edit_app
from dairy_bot.services.git_sync import GitPushError
from dairy_bot.services.note_editing import note_text_sha256


class FakeGit:
    def __init__(self, *, fail_push: bool = False, mutate_on_prepare=None):
        self.fail_push = fail_push
        self.mutate_on_prepare = mutate_on_prepare
        self.prepare_calls = 0
        self.committed_paths = []

    def prepare_for_write(self):
        self.prepare_calls += 1
        if self.mutate_on_prepare is not None:
            self.mutate_on_prepare()

    def commit_and_push(self, paths):
        self.committed_paths.append(list(paths))
        if self.fail_push:
            raise GitPushError("push failed")
        return SimpleNamespace(pushed=True)


def write_note(tmp_path, content: str):
    path = tmp_path / "2026" / "06" / "2026-06-16.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def payload(expected_sha256: str, new_text: str = "Updated text."):
    return {
        "note_id": "2026-06-16T09:00",
        "note_path": "2026/06/2026-06-16.md",
        "expected_sha256": expected_sha256,
        "new_text": new_text,
    }


async def request(app, body, *, token: str = "secret"):
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/internal/notes/replace-text",
            json=body,
            headers={"X-Edit-Token": token},
        )
        return response.status, await response.json()
    finally:
        await client.close()


def make_app(tmp_path, git: FakeGit):
    settings = SimpleNamespace(journal_dir=tmp_path, edit_api_token=SecretStr("secret"))
    return create_edit_app(settings, git)


def test_edit_api_happy_path_replaces_text_and_commits(tmp_path):
    path = write_note(tmp_path, "## 09:00\n\nOriginal text.\n")
    git = FakeGit()

    status, body = asyncio.run(
        request(make_app(tmp_path, git), payload(note_text_sha256("Original text.")))
    )

    assert status == 200
    assert body == {"new_sha256": note_text_sha256("Updated text.")}
    assert path.read_text(encoding="utf-8") == "## 09:00\n\nUpdated text.\n\n"
    assert git.prepare_calls == 1
    assert git.committed_paths == [[path]]


def test_edit_api_rejects_bad_token_and_validation_without_changing_file(tmp_path):
    path = write_note(tmp_path, "## 09:00\n\nOriginal text.\n")
    unauthorized = asyncio.run(
        request(make_app(tmp_path, FakeGit()), payload(note_text_sha256("Original text.")), token="wrong")
    )
    invalid = asyncio.run(
        request(make_app(tmp_path, FakeGit()), payload(note_text_sha256("Original text."), "bad\n## 12:34"))
    )

    assert unauthorized[0] == 401
    assert invalid[0] == 422
    assert path.read_text(encoding="utf-8") == "## 09:00\n\nOriginal text.\n"


def test_edit_api_conflict_and_missing_note_do_not_change_file(tmp_path):
    path = write_note(tmp_path, "## 09:00\n\nOriginal text.\n")
    conflict = asyncio.run(request(make_app(tmp_path, FakeGit()), payload(note_text_sha256("stale"))))
    missing_body = payload(note_text_sha256("Original text."))
    missing_body["note_id"] = "2026-06-16T10:00"
    missing = asyncio.run(request(make_app(tmp_path, FakeGit()), missing_body))

    assert conflict[0] == 409
    assert missing[0] == 404
    assert path.read_text(encoding="utf-8") == "## 09:00\n\nOriginal text.\n"


def test_edit_api_pull_changes_same_block_before_hash_check_causes_conflict(tmp_path):
    path = write_note(tmp_path, "## 09:00\n\nOriginal text.\n")

    def mutate():
        path.write_text("## 09:00\n\nRemote text.\n", encoding="utf-8")

    git = FakeGit(mutate_on_prepare=mutate)
    status, _ = asyncio.run(
        request(make_app(tmp_path, git), payload(note_text_sha256("Original text.")))
    )

    assert status == 409
    assert path.read_text(encoding="utf-8") == "## 09:00\n\nRemote text.\n"
    assert git.committed_paths == []


def test_edit_api_push_failure_still_returns_success_after_local_write(tmp_path):
    path = write_note(tmp_path, "## 09:00\n\nOriginal text.\n")
    git = FakeGit(fail_push=True)

    status, body = asyncio.run(
        request(make_app(tmp_path, git), payload(note_text_sha256("Original text.")))
    )

    assert status == 200
    assert body["new_sha256"] == note_text_sha256("Updated text.")
    assert "Updated text." in path.read_text(encoding="utf-8")
    assert git.committed_paths == [[path]]
