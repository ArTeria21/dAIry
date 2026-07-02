from __future__ import annotations

import pytest

from dairy_bot.services.note_editing import (
    NoteEditConflict,
    NoteEditNotFound,
    NoteEditValidationError,
    note_text_sha256,
    replace_note_text,
    validate_new_text,
)


def test_replace_note_text_preserves_frontmatter_neighbors_header_and_enrichment():
    content = "\n".join(
        [
            "---",
            "summary: Existing",
            "---",
            "",
            "## 09:00 — text",
            "",
            "Original text.",
            "<!-- dairy:note-enrichment -->",
            "mood:: calm · topics:: work",
            "",
            "## 10:00 — voice",
            "",
            "Neighbor text.",
            "",
        ]
    )

    result = replace_note_text(
        content=content,
        note_id="2026-06-16T09:00",
        note_path="2026/06/2026-06-16.md",
        expected_sha256=note_text_sha256("Original text."),
        new_text="Updated text.",
    )

    assert result.new_sha256 == note_text_sha256("Updated text.")
    assert result.content.startswith("---\nsummary: Existing\n---")
    assert "## 09:00 — text\n\nUpdated text.\n\n<!-- dairy:note-enrichment -->\nmood:: calm · topics:: work\n" in result.content
    assert "## 10:00 — voice\n\nNeighbor text." in result.content
    assert "Original text." not in result.content


def test_note_text_sha256_uses_trimmed_canonical_text():
    assert note_text_sha256("\nOriginal text.\n\n") == note_text_sha256("Original text.")


def test_replace_note_text_matches_duplicate_ids_and_keeps_postfactum_header():
    content = "\n".join(
        [
            "# 2026-06-16",
            "",
            "## June 16 21:55",
            "",
            "First text.",
            "",
            "## June 16 21:55",
            "",
            "Second text.",
        ]
    )

    result = replace_note_text(
        content=content,
        note_id="2026-06-16T21:55#2",
        note_path="2026/06/2026-06-16.md",
        expected_sha256=note_text_sha256("Second text."),
        new_text="Second updated.",
    )

    assert "## June 16 21:55\n\nFirst text." in result.content
    assert "## June 16 21:55\n\nSecond updated.\n" in result.content
    assert result.content.endswith("\n")


def test_replace_note_text_allows_unmodified_neighbor_blocks_added_after_get():
    content = "\n".join(
        [
            "## 09:00",
            "",
            "Editable text.",
            "",
            "## 11:00",
            "",
            "Newer unrelated text.",
        ]
    )

    result = replace_note_text(
        content=content,
        note_id="2026-06-16T09:00",
        note_path="2026/06/2026-06-16.md",
        expected_sha256=note_text_sha256("Editable text."),
        new_text="Edited text.",
    )

    assert "Edited text." in result.content
    assert "## 11:00\n\nNewer unrelated text." in result.content


def test_replace_note_text_rejects_stale_hash_and_missing_blocks_without_changes():
    content = "## 09:00\n\nOriginal text.\n"

    with pytest.raises(NoteEditConflict):
        replace_note_text(
            content=content,
            note_id="2026-06-16T09:00",
            note_path="2026/06/2026-06-16.md",
            expected_sha256=note_text_sha256("Other text."),
            new_text="Updated.",
        )

    with pytest.raises(NoteEditNotFound):
        replace_note_text(
            content=content,
            note_id="2026-06-16T10:00",
            note_path="2026/06/2026-06-16.md",
            expected_sha256=note_text_sha256("Original text."),
            new_text="Updated.",
        )


@pytest.mark.parametrize(
    "new_text",
    [
        "   ",
        "Intro\n## 12:34\nBad heading",
        "Intro\n## arbitrary heading",
        "Intro\n<!-- dairy:note-enrichment -->",
    ],
)
def test_validate_new_text_rejects_unsafe_replacements(new_text: str):
    with pytest.raises(NoteEditValidationError):
        validate_new_text(new_text)
