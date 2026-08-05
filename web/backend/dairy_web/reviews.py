from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote


class ReviewReadStore:
    """Read-only projection adapter for the bot-owned reviews database."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        path = quote(str(self.db_path.resolve()), safe="/:")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def list_reviews(self, kind: str) -> list[dict[str, object]]:
        if not self.db_path.is_file():
            return []
        with self.connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM reviews
                    WHERE kind = ? AND status IN ('ready', 'stale')
                    ORDER BY start_date DESC
                    """,
                    (kind,),
                ).fetchall()
            except sqlite3.OperationalError as error:
                if _is_missing_table_error(error):
                    return []
                raise
            audit = _audit_by_key(connection)
        return [_review_dict(row, audit) for row in rows]

    def get_review(self, kind: str, period: str) -> dict[str, object] | None:
        if not self.db_path.is_file():
            return None
        with self.connect() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT * FROM reviews
                    WHERE kind = ? AND period = ?
                      AND status IN ('ready', 'stale')
                    """,
                    (kind, period),
                ).fetchone()
            except sqlite3.OperationalError as error:
                if _is_missing_table_error(error):
                    return None
                raise
            audit = _audit_by_key(connection)
        return None if row is None else _review_dict(row, audit)

    def list_sources(self, kind: str, period: str) -> list[dict[str, object]]:
        if not self.db_path.is_file():
            return []
        with self.connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT source_id, source_type, label, position
                    FROM review_sources
                    WHERE kind = ? AND period = ?
                    ORDER BY position
                    """,
                    (kind, period),
                ).fetchall()
            except sqlite3.OperationalError as error:
                if _is_missing_table_error(error):
                    return []
                raise
        return [dict(row) for row in rows]


class NullReviewReadStore:
    def list_reviews(self, kind: str) -> list[dict[str, object]]:
        return []

    def get_review(self, kind: str, period: str) -> None:
        return None

    def list_sources(self, kind: str, period: str) -> list[dict[str, object]]:
        return []


def _audit_by_key(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], sqlite3.Row]:
    try:
        rows = connection.execute("SELECT * FROM review_audit").fetchall()
    except sqlite3.OperationalError as error:
        if _is_missing_table_error(error):
            return {}
        raise
    return {(row["kind"], row["period"]): row for row in rows}


def _is_missing_table_error(error: sqlite3.OperationalError) -> bool:
    return "no such table" in str(error).lower()


def _review_dict(
    row: sqlite3.Row,
    audit: dict[tuple[str, str], sqlite3.Row],
) -> dict[str, object]:
    result = dict(row)
    result["payload"] = json.loads(str(row["payload"]))
    audit_row = audit.get((str(row["kind"]), str(row["period"])))
    result["version"] = 1 if audit_row is None else int(audit_row["version"])
    result["created_at"] = None if audit_row is None else audit_row["created_at"]
    result["updated_at"] = None if audit_row is None else audit_row["updated_at"]
    return result
