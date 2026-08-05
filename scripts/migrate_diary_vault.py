from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import SecretStr
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dairy_bot.config import DEFAULT_TZ, DEFAULT_TZ_NAME  # noqa: E402
from dairy_bot.services.enrichment import (  # noqa: E402
    DailyEntry,
    DayEnrichmentFailure,
    NoteEnrichmentFailure,
    _derived_date_fields,
    _update_frontmatter,
    enrich_daily_note_notes,
    parse_daily_entries,
    read_text,
    write_text,
)
from dairy_bot.services.enrichment_client import OpenRouterEnrichmentClient  # noqa: E402
from dairy_bot.services.enrichment_db import EnrichmentStore  # noqa: E402
from dairy_bot.services.enrichment_schemas import DayEnrichment  # noqa: E402


DEFAULT_NOTE_CONCURRENCY = 4
DEFAULT_DAY_CONCURRENCY = 3

DAILY_FILE_RE = re.compile(r"^\d{4}/\d{2}/\d{4}-\d{2}-\d{2}\.md$")
DATE_HEADER_RE = re.compile(r"^#\s+\d{4}-\d{2}-\d{2}\s*$")
CURRENT_NAV_RE = re.compile(
    r"^(?:\[\[[^\]]+\|Prev day\]\](?:\s*·\s*\[\[[^\]]+\|Next day\]\])?"
    r"|\[\[[^\]]+\|Next day\]\])$"
)
WIKILINK_ONLY_RE = re.compile(r"^\[\[[^\]]+\]\]$")
LEGACY_STAMP_RE = re.compile(
    r"^\*?(?P<day>\d{1,2})-(?P<month>\d{1,2})-(?P<year>\d{4})\s*\|\s*"
    r"(?P<hour>\d{1,2})[-:](?P<minute>\d{2})\*?$"
)


class MigrationClient(Protocol):
    async def enrich_note(self, text: str): ...

    async def embed_note(self, text: str) -> list[float]: ...

    async def enrich_day(self, text: str) -> DayEnrichment: ...


@dataclass(slots=True)
class ManualOverrides:
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def has_values(self) -> bool:
        return bool(self.facts)


@dataclass(slots=True)
class MigratedEntry:
    timestamp: str
    kind: str
    text: str


@dataclass(slots=True)
class ScannedNote:
    path: Path
    date: date
    raw_text: str
    frontmatter: dict[str, Any]
    entries: list[MigratedEntry]
    manual_overrides: ManualOverrides
    in_range: bool
    normalized_content: str = ""

    @property
    def contentful(self) -> bool:
        return bool(self.entries)


@dataclass(slots=True)
class MigrationConfig:
    journal_dir: Path
    enrichment_db_path: Path
    timezone: ZoneInfo = DEFAULT_TZ
    language: str = "EN"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    enrichment_model_name: str = "openai/gpt-4.1-mini"
    embedding_model_name: str = "openai/text-embedding-3-small"

    @classmethod
    def from_env(cls, env_file: Path = PROJECT_ROOT / ".env") -> "MigrationConfig":
        values = _load_env(env_file)
        journal_dir = _resolve_journal_dir(
            values.get("JOURNAL_DIR")
            or values.get("JOURNAL_PATH")
            or str(PROJECT_ROOT / "diary")
        )
        db_path = _resolve_path(values.get("ENRICHMENT_DB_PATH") or "data/enrichment.sqlite3")
        timezone = _parse_timezone(values.get("TIMEZONE") or values.get("PREFERRED_TIMEZONE"))
        return cls(
            journal_dir=journal_dir,
            enrichment_db_path=db_path,
            timezone=timezone,
            language=values.get("LANGUAGE", "EN"),
            openrouter_api_key=values.get("OPENROUTER_API_KEY"),
            openrouter_base_url=values.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            enrichment_model_name=values.get(
                "ENRICHMENT_MODEL_NAME", "openai/gpt-4.1-mini"
            ),
            embedding_model_name=values.get(
                "EMBEDDING_MODEL_NAME", "openai/text-embedding-3-small"
            ),
        )

    def as_client_settings(self) -> SimpleNamespace:
        if not self.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required for --apply")
        return SimpleNamespace(
            openrouter_base_url=self.openrouter_base_url,
            openrouter_api_key=SecretStr(self.openrouter_api_key),
            enrichment_model_name=self.enrichment_model_name,
            embedding_model_name=self.embedding_model_name,
            language=self.language,
        )


@dataclass(slots=True)
class MigrationStats:
    total_files: int = 0
    deleted_files: int = 0
    rewritten_files: int = 0
    note_entries: int = 0
    day_enrichments: int = 0


def discover_daily_note_paths(journal_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in journal_dir.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(journal_dir).as_posix()
        except ValueError:
            continue
        if DAILY_FILE_RE.match(rel):
            paths.append(path)
    return sorted(paths)


def scan_notes(
    journal_dir: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    show_progress: bool = True,
) -> list[ScannedNote]:
    paths = discover_daily_note_paths(journal_dir)
    iterator = tqdm(paths, desc="scan", unit="file", disable=not show_progress)
    notes = [_scan_note(path, start=start, end=end) for path in iterator]
    contentful_dates = [note.date for note in notes if note.contentful]
    nav_by_date = _navigation_by_date(contentful_dates)
    for note in notes:
        if note.contentful:
            prev_day, next_day = nav_by_date[note.date]
            note.normalized_content = render_daily_note(note, prev_day, next_day)
    return notes


def render_daily_note(
    note: ScannedNote,
    prev_day: date | None,
    next_day: date | None,
) -> str:
    date_label = note.date.isoformat()
    lines = [
        "---",
        f"date: {date_label}",
        "type: daily",
        "---",
        f"# {date_label}",
    ]
    nav_line = _format_nav(prev_day, next_day)
    lines.append(nav_line)
    lines.append("")
    for entry in note.entries:
        kind = "voice" if entry.kind == "voice" else "text"
        lines.append(f"## {entry.timestamp} — {kind}")
        lines.append("")
        lines.extend(entry.text.strip().splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def manual_overrides_from_frontmatter(frontmatter: dict[str, Any]) -> ManualOverrides:
    facts: dict[str, Any] = {}
    evidence: dict[str, str] = {}
    habits = frontmatter.get("habits")
    if not isinstance(habits, dict):
        habits = {}

    sport = _coerce_bool(frontmatter.get("sport"))
    if sport is not None:
        _set_manual(facts, evidence, "sport", sport, "legacy sport")

    reading = _coerce_bool(habits.get("reading"))
    if reading is not None:
        _set_manual(facts, evidence, "reading", reading, "legacy habits.reading")

    no_eating_out = _coerce_bool(habits.get("no_eating_out"))
    if no_eating_out is not None:
        _set_manual(
            facts,
            evidence,
            "eating_outside",
            not no_eating_out,
            "legacy habits.no_eating_out",
        )

    zero_spending = _coerce_bool(habits.get("zero_spending"))
    if zero_spending is not None:
        _set_manual(
            facts,
            evidence,
            "purchases",
            not zero_spending,
            "legacy habits.zero_spending",
        )

    sleep_quality = _sleep_quality_from_legacy(frontmatter)
    if sleep_quality is not None:
        _set_manual(
            facts,
            evidence,
            "sleep_quality",
            sleep_quality,
            "legacy sleep_score/sleep_duration",
        )

    focus = _coerce_number(frontmatter.get("focus"))
    if focus is not None:
        if focus >= 4:
            _set_manual(facts, evidence, "deep_focus", True, "legacy focus")
        elif focus <= 2:
            _set_manual(facts, evidence, "deep_focus", False, "legacy focus")

    return ManualOverrides(facts=facts, evidence=evidence)


async def run_migration(
    config: MigrationConfig,
    *,
    apply: bool = False,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
    allow_dirty: bool = False,
    client: MigrationClient | None = None,
    note_concurrency: int = DEFAULT_NOTE_CONCURRENCY,
    day_concurrency: int = DEFAULT_DAY_CONCURRENCY,
    show_progress: bool = True,
) -> MigrationStats:
    notes = scan_notes(
        config.journal_dir,
        start=start,
        end=end,
        show_progress=show_progress,
    )
    selected = [note for note in notes if note.in_range]
    selected_contentful = [note for note in selected if note.contentful]
    selected_empty = [note for note in selected if not note.contentful]
    stats = MigrationStats(
        total_files=len(selected),
        deleted_files=len(selected_empty),
        rewritten_files=sum(
            1
            for note in selected_contentful
            if force or note.raw_text != note.normalized_content
        ),
        note_entries=sum(len(note.entries) for note in selected_contentful),
        day_enrichments=len(selected_contentful),
    )
    if not apply:
        return stats

    ensure_clean_journal_git(config.journal_dir, allow_dirty=allow_dirty)
    _delete_empty_notes(selected_empty, show_progress=show_progress)
    _rewrite_contentful_notes(selected_contentful, force=force, show_progress=show_progress)
    _reset_enrichment_db(config.enrichment_db_path)
    store = EnrichmentStore(config.enrichment_db_path)
    owned_client = client is None
    if client is None:
        client = OpenRouterEnrichmentClient(config.as_client_settings())
    try:
        await _run_note_enrichment(
            selected_contentful,
            config.journal_dir,
            client,
            store,
            concurrency=note_concurrency,
            show_progress=show_progress,
        )
        await _run_day_enrichment(
            selected_contentful,
            config.journal_dir,
            client,
            store,
            config.timezone,
            concurrency=day_concurrency,
            show_progress=show_progress,
        )
    finally:
        if owned_client:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
    return stats


def ensure_clean_journal_git(journal_dir: Path, *, allow_dirty: bool) -> None:
    if allow_dirty or not (journal_dir / ".git").exists():
        return
    result = subprocess.run(
        ["git", "-C", str(journal_dir), "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not inspect nested diary git repo: {result.stderr}")
    if result.stdout.strip():
        raise RuntimeError(
            "Nested diary git repo has local changes. Commit/stash them or pass "
            "--allow-dirty."
        )


def print_stats(stats: MigrationStats, *, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    print(f"Migration {mode} summary")
    print(f"  daily files considered: {stats.total_files}")
    print(f"  files to delete:        {stats.deleted_files}")
    print(f"  files to rewrite:       {stats.rewritten_files}")
    print(f"  note entries:           {stats.note_entries}")
    print(f"  day enrichments:        {stats.day_enrichments}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize a legacy dAIry vault and rebuild enrichment SQLite."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write files and rebuild DB")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="only print counts; this is the default",
    )
    parser.add_argument("--from", dest="date_from", type=_parse_date_arg)
    parser.add_argument("--to", dest="date_to", type=_parse_date_arg)
    parser.add_argument("--force", action="store_true", help="rewrite even no-op files")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow applying with local changes in the nested diary git repo",
    )
    parser.add_argument("--journal-dir", type=Path, help="override JOURNAL_DIR")
    parser.add_argument("--db-path", type=Path, help="override ENRICHMENT_DB_PATH")
    parser.add_argument(
        "--note-concurrency",
        type=_parse_positive_int_arg,
        default=DEFAULT_NOTE_CONCURRENCY,
        help=(
            "number of daily files to process concurrently during note-level "
            f"enrichment (default: {DEFAULT_NOTE_CONCURRENCY})"
        ),
    )
    parser.add_argument(
        "--day-concurrency",
        type=_parse_positive_int_arg,
        default=DEFAULT_DAY_CONCURRENCY,
        help=(
            "number of daily files to process concurrently during day-level "
            f"enrichment (default: {DEFAULT_DAY_CONCURRENCY})"
        ),
    )
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = MigrationConfig.from_env()
    if args.journal_dir is not None:
        config.journal_dir = args.journal_dir
    if args.db_path is not None:
        config.enrichment_db_path = args.db_path
    stats = await run_migration(
        config,
        apply=args.apply,
        start=args.date_from,
        end=args.date_to,
        force=args.force,
        allow_dirty=args.allow_dirty,
        note_concurrency=args.note_concurrency,
        day_concurrency=args.day_concurrency,
    )
    print_stats(stats, apply=args.apply)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


def _scan_note(path: Path, *, start: date | None, end: date | None) -> ScannedNote:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw_text)
    note_date = date.fromisoformat(path.stem)
    entries = _extract_entries(raw_text, body, path)
    return ScannedNote(
        path=path,
        date=note_date,
        raw_text=raw_text,
        frontmatter=frontmatter,
        entries=entries,
        manual_overrides=manual_overrides_from_frontmatter(frontmatter),
        in_range=_date_in_range(note_date, start=start, end=end),
    )


def _extract_entries(raw_text: str, body: str, path: Path) -> list[MigratedEntry]:
    parsed = parse_daily_entries(raw_text, path)
    if parsed:
        return [_entry_from_daily_entry(entry) for entry in parsed]

    cleaned = _clean_legacy_body(body)
    if not cleaned.strip():
        return []
    return [
        MigratedEntry(
            timestamp=_legacy_timestamp(body) or "12:00",
            kind="text",
            text=cleaned,
        )
    ]


def _entry_from_daily_entry(entry: DailyEntry) -> MigratedEntry:
    kind = "voice" if entry.kind == "voice" else "text"
    return MigratedEntry(timestamp=entry.timestamp, kind=kind, text=entry.text)


def _clean_legacy_body(body: str) -> str:
    lines = body.splitlines()
    changed = True
    while changed:
        changed = False
        while lines and not lines[0].strip():
            lines.pop(0)
            changed = True
        if lines and DATE_HEADER_RE.match(lines[0].strip()):
            lines.pop(0)
            changed = True
            continue
        if lines and _looks_like_nav_or_shell(lines[0]):
            lines.pop(0)
            changed = True
            continue
        if lines and lines[0].strip() == "---":
            lines.pop(0)
            changed = True
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _looks_like_nav_or_shell(line: str) -> bool:
    stripped = line.strip()
    return bool(
        CURRENT_NAV_RE.match(stripped)
        or WIKILINK_ONLY_RE.match(stripped)
        or LEGACY_STAMP_RE.match(stripped)
    )


def _legacy_timestamp(body: str) -> str | None:
    for line in body.splitlines()[:8]:
        match = LEGACY_STAMP_RE.match(line.strip())
        if match is None:
            continue
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "".join(lines[1:index])
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            return data, "".join(lines[index + 1 :])
    return {}, content


def _navigation_by_date(dates: list[date]) -> dict[date, tuple[date | None, date | None]]:
    unique = sorted(set(dates))
    nav: dict[date, tuple[date | None, date | None]] = {}
    for index, value in enumerate(unique):
        prev_day = unique[index - 1] if index > 0 else None
        next_day = unique[index + 1] if index + 1 < len(unique) else None
        nav[value] = (prev_day, next_day)
    return nav


def _format_nav(prev_day: date | None, next_day: date | None) -> str:
    links: list[str] = []
    if prev_day is not None:
        links.append(f"[[{prev_day.isoformat()}|Prev day]]")
    if next_day is not None:
        links.append(f"[[{next_day.isoformat()}|Next day]]")
    return " · ".join(links)


def _date_in_range(value: date, *, start: date | None, end: date | None) -> bool:
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


def _delete_empty_notes(notes: list[ScannedNote], *, show_progress: bool) -> None:
    iterator = tqdm(notes, desc="delete", unit="file", disable=not show_progress)
    for note in iterator:
        note.path.unlink(missing_ok=True)


def _rewrite_contentful_notes(
    notes: list[ScannedNote],
    *,
    force: bool,
    show_progress: bool,
) -> None:
    iterator = tqdm(notes, desc="rewrite", unit="file", disable=not show_progress)
    for note in iterator:
        if force or note.raw_text != note.normalized_content:
            note.path.write_text(note.normalized_content, encoding="utf-8")


def _reset_enrichment_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ):
        path.unlink(missing_ok=True)


async def _run_note_enrichment(
    notes: list[ScannedNote],
    journal_dir: Path,
    client: MigrationClient,
    store: EnrichmentStore,
    *,
    concurrency: int,
    show_progress: bool,
) -> None:
    async def enrich_note_file(note: ScannedNote) -> None:
        try:
            await enrich_daily_note_notes(note.path, journal_dir, client, store)
        except NoteEnrichmentFailure:
            raise

    await _run_bounded(
        notes,
        enrich_note_file,
        concurrency=concurrency,
        desc="note enrichment",
        unit="day",
        show_progress=show_progress,
    )


async def _run_day_enrichment(
    notes: list[ScannedNote],
    journal_dir: Path,
    client: MigrationClient,
    store: EnrichmentStore,
    timezone: ZoneInfo,
    *,
    concurrency: int,
    show_progress: bool,
) -> None:
    async def enrich_day_file(note: ScannedNote) -> None:
        try:
            await _enrich_day_with_manual_overrides(
                note, journal_dir, client, store, timezone
            )
        except Exception as exc:
            raise DayEnrichmentFailure(f"Failed to enrich day {note.path}") from exc

    await _run_bounded(
        notes,
        enrich_day_file,
        concurrency=concurrency,
        desc="day enrichment",
        unit="day",
        show_progress=show_progress,
    )


async def _run_bounded(
    items: list[ScannedNote],
    worker: Callable[[ScannedNote], Awaitable[None]],
    *,
    concurrency: int,
    desc: str,
    unit: str,
    show_progress: bool,
) -> None:
    if not items:
        return

    queue: asyncio.Queue[ScannedNote | None] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    worker_count = min(max(concurrency, 1), len(items))
    for _ in range(worker_count):
        queue.put_nowait(None)

    progress = tqdm(total=len(items), desc=desc, unit=unit, disable=not show_progress)
    first_error: BaseException | None = None
    error_lock = asyncio.Lock()

    async def run_worker() -> None:
        nonlocal first_error
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                async with error_lock:
                    if first_error is not None:
                        continue
                await worker(item)
                progress.update(1)
            except BaseException as exc:
                async with error_lock:
                    if first_error is None:
                        first_error = exc
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(run_worker()) for _ in range(worker_count)]
    try:
        await queue.join()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        progress.close()

    if first_error is not None:
        raise first_error


async def _enrich_day_with_manual_overrides(
    note: ScannedNote,
    journal_dir: Path,
    client: MigrationClient,
    store: EnrichmentStore,
    timezone: ZoneInfo,
) -> bool:
    content = await read_text(note.path)
    enrichment = await client.enrich_day(content)
    enrichment = _merge_manual_overrides(enrichment, note.manual_overrides)
    derived = _derived_date_fields(note.date)
    updated = _update_frontmatter(content, enrichment, derived)
    changed = updated != content
    if changed:
        await write_text(note.path, updated)
    store.upsert_day(
        date=note.date.isoformat(),
        enrichment=enrichment,
        weekday=derived["weekday"],
        is_weekend=bool(derived["is_weekend"]),
        season=str(derived["season"]),
    )
    return changed


def _merge_manual_overrides(
    enrichment: DayEnrichment,
    overrides: ManualOverrides,
) -> DayEnrichment:
    if not overrides.has_values:
        return enrichment
    updates: dict[str, Any] = {}
    for field_name, value in overrides.facts.items():
        updates[field_name] = value
        evidence_field = f"{field_name}_evidence"
        if evidence_field in DayEnrichment.model_fields:
            updates[evidence_field] = overrides.evidence[field_name]
    return enrichment.model_copy(update=updates)


def _set_manual(
    facts: dict[str, Any],
    evidence: dict[str, str],
    field_name: str,
    value: Any,
    source: str,
) -> None:
    facts[field_name] = value
    evidence[field_name] = f"Manual metadata: {source} was set in legacy YAML."


def _sleep_quality_from_legacy(frontmatter: dict[str, Any]) -> int | None:
    sleep_score = _coerce_number(frontmatter.get("sleep_score"))
    if sleep_score is not None:
        if sleep_score >= 85:
            return 5
        if sleep_score >= 75:
            return 4
        if sleep_score >= 60:
            return 3
        if sleep_score >= 40:
            return 2
        return 1

    sleep_duration = _coerce_number(frontmatter.get("sleep_duration"))
    if sleep_duration is None:
        return None
    if sleep_duration >= 480:
        return 5
    if sleep_duration >= 420:
        return 4
    if sleep_duration >= 360:
        return 3
    if sleep_duration >= 300:
        return 2
    return 1


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _load_env(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = _strip_quotes(value.strip())
    values.update(os.environ)
    return values


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _resolve_journal_dir(value: str) -> Path:
    path = _resolve_path(value)
    local_diary = PROJECT_ROOT / "diary"
    if not path.exists() and value == "/data" and local_diary.exists():
        return local_diary
    return path


def _parse_timezone(value: str | None) -> ZoneInfo:
    if not value:
        return DEFAULT_TZ
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TZ_NAME)


def _parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _parse_positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
