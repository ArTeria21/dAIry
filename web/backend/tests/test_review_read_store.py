from __future__ import annotations

import sqlite3

import pytest

from dairy_web.reviews import ReviewReadStore


@pytest.mark.parametrize("initialize_sqlite", [False, True])
def test_uninitialized_reviews_database_is_an_empty_projection(
    tmp_path,
    initialize_sqlite: bool,
):
    db_path = tmp_path / "reviews.sqlite3"
    if initialize_sqlite:
        with sqlite3.connect(db_path):
            pass
    else:
        db_path.write_bytes(b"")

    store = ReviewReadStore(db_path)

    assert store.list_reviews("week") == []
    assert store.get_review("week", "2026-07-26") is None
    assert store.list_sources("week", "2026-07-26") == []


def test_missing_reviews_database_is_an_empty_projection(tmp_path):
    store = ReviewReadStore(tmp_path / "reviews.sqlite3")

    assert store.list_reviews("month") == []
    assert store.get_review("month", "2026-07") is None
    assert store.list_sources("month", "2026-07") == []


def test_stale_review_remains_readable_while_replacement_is_pending(tmp_path):
    db_path = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE reviews (
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                start_date TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            INSERT INTO reviews VALUES
                ('week', '2026-07-26', '2026-07-26', 'stale', '{}'),
                ('week', '2026-07-19', '2026-07-19', 'ready', '{}'),
                ('week', '2026-07-12', '2026-07-12', 'failed', '{}');
            """
        )

    store = ReviewReadStore(db_path)

    assert [row["period"] for row in store.list_reviews("week")] == [
        "2026-07-26",
        "2026-07-19",
    ]
    assert store.get_review("week", "2026-07-26")["status"] == "stale"
    assert store.get_review("week", "2026-07-12") is None
