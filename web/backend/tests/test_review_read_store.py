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
