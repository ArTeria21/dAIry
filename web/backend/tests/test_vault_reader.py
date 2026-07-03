from __future__ import annotations

import pytest

from dairy_web.vault_reader import (
    DayNotFound,
    extract_note_raw_text,
    extract_note_raw_text_by_id,
    identify_day_note_blocks,
    list_day_dates,
    raw_text_sha256,
    read_day,
)


def write_day(vault_dir, raw_date: str, content: str) -> None:
    path = vault_dir / raw_date[:4] / raw_date[5:7] / f"{raw_date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_read_day_parses_blocks_in_file_order_and_keeps_duplicate_timestamps(tmp_path):
    write_day(
        tmp_path,
        "2026-06-16",
        "\n".join(
            [
                "# 2026-06-16",
                "",
                "## 09:00 — text",
                "",
                "First block.",
                "<!-- dairy:note-enrichment -->",
                "mood:: calm · topics:: work",
                "",
                "## 09:00 — voice",
                "Second block.",
                "",
                "## June 16 21:55",
                "Postfactum note.",
                "### Inner heading stays raw text",
            ]
        ),
    )

    blocks = read_day(vault_dir=tmp_path, day="2026-06-16")

    assert [block.ts for block in blocks] == ["09:00", "09:00", "21:55"]
    assert [block.kind for block in blocks] == ["text", "voice", None]
    assert blocks[0].heading_display == "09:00 — text"
    assert blocks[0].raw_text == "First block."
    assert blocks[2].heading_display == "June 16 21:55"
    assert blocks[2].raw_text == "Postfactum note.\n### Inner heading stays raw text"
    assert extract_note_raw_text(
        vault_dir=tmp_path,
        note_path="2026/06/2026-06-16.md",
        ts="09:00",
    ) == "First block."
    assert extract_note_raw_text_by_id(
        vault_dir=tmp_path,
        note_path="2026/06/2026-06-16.md",
        note_id="2026-06-16T09:00#2",
    ) == "Second block."


def test_extract_note_raw_text_by_id_matches_exact_duplicate_timestamp_block(tmp_path):
    write_day(
        tmp_path,
        "2026-06-08",
        "\n".join(
            [
                "# 2026-06-08",
                "",
                "## 10:00",
                "",
                "first",
                "",
                "## 10:00",
                "",
                "second",
            ]
        ),
    )

    assert extract_note_raw_text_by_id(
        vault_dir=tmp_path,
        note_path="2026/06/2026-06-08.md",
        note_id="2026-06-08T10:00",
    ) == "first"
    assert extract_note_raw_text_by_id(
        vault_dir=tmp_path,
        note_path="2026/06/2026-06-08.md",
        note_id="2026-06-08T10:00#2",
    ) == "second"


def test_identified_day_blocks_skip_empty_blocks_when_numbering_duplicates(tmp_path):
    write_day(
        tmp_path,
        "2026-06-16",
        "\n".join(
            [
                "# 2026-06-16",
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
            ]
        ),
    )

    blocks = read_day(vault_dir=tmp_path, day="2026-06-16")
    identified = identify_day_note_blocks(blocks=blocks, target_date="2026-06-16")

    assert [(item.id, item.block.raw_text) for item in identified] == [
        ("2026-06-16T10:00", "second block text"),
        ("2026-06-16T10:00#2", "third block text"),
    ]
    assert (
        extract_note_raw_text_by_id(
            vault_dir=tmp_path,
            note_path="2026/06/2026-06-16.md",
            note_id="2026-06-16T10:00#2",
        )
        == "third block text"
    )


def test_read_day_allows_existing_files_without_note_blocks(tmp_path):
    write_day(
        tmp_path,
        "2026-06-17",
        "# 2026-06-17\n\nA loose day page with no note headings.",
    )

    assert read_day(vault_dir=tmp_path, day="2026-06-17") == []


def test_list_day_dates_returns_sorted_valid_vault_files_only(tmp_path):
    write_day(tmp_path, "2026-06-18", "## 11:00\nLater.")
    write_day(tmp_path, "2026-06-16", "## 09:00\nEarlier.")
    invalid = tmp_path / "2026" / "99" / "2026-99-01.md"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("## 09:00\nInvalid.", encoding="utf-8")

    assert list_day_dates(vault_dir=tmp_path) == ["2026-06-16", "2026-06-18"]


def test_read_day_missing_file_raises_sanitized_error(tmp_path):
    with pytest.raises(DayNotFound, match="Day not found"):
        read_day(vault_dir=tmp_path, day="2026-06-16")


def test_raw_text_sha256_uses_trimmed_canonical_text():
    assert raw_text_sha256("\nOriginal text.\n\n") == raw_text_sha256("Original text.")
