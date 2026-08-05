from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from dairy_bot.services.reviews import scan_corpus


FIRST_SEEN = datetime(2026, 8, 4, 12, 0, tzinfo=ZoneInfo("Europe/Vienna"))


def test_AC_1_corpus_indexes_daily_entries_and_ignores_other_markdown(tmp_path):
    note = tmp_path / "notes" / "plain.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ncreated: 2026-07-12\n---\nA thought without a Markdown heading.\n",
        encoding="utf-8",
    )
    daily = tmp_path / "2026" / "07" / "2026-07-12.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        "---\ndate: 2026-07-12\ntype: daily\n---\n"
        "# 2026-07-12\n\n## 09:30\n\nA real diary entry.\n",
        encoding="utf-8",
    )

    documents = scan_corpus(tmp_path, first_seen=FIRST_SEEN)

    assert [(document.document_id, document.heading, document.text) for document in documents] == [
        (
            "diary:2026-07-12T09:30",
            "09:30",
            "A real diary entry.",
        )
    ]


def test_AC_1_corpus_returns_no_documents_for_non_daily_markdown(tmp_path):
    note = tmp_path / "notes" / "patterns.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# Reflection\nFirst distinct observation.\n\n"
        "## Reflection\nSecond distinct observation.\n",
        encoding="utf-8",
    )

    documents = scan_corpus(tmp_path, first_seen=FIRST_SEEN)

    assert documents == []
