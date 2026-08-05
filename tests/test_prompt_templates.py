from __future__ import annotations

import re
from pathlib import Path

import pytest

from dairy_bot.prompts import load_prompt


PROMPTS_ROOT = Path(__file__).parents[1] / "src" / "dairy_bot" / "prompts"
PLACEHOLDER_RE = re.compile(r"{([a-z][a-z0-9_]*)}")


def test_prompts_are_grouped_in_named_subdirectories() -> None:
    assert not list(PROMPTS_ROOT.glob("*.md"))
    assert {
        path.name
        for path in PROMPTS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    } == {
        "clusters",
        "enrichment",
        "review",
        "toc",
        "voice",
    }


def test_every_markdown_template_loads_and_replaces_its_placeholders() -> None:
    templates = sorted(PROMPTS_ROOT.rglob("*.md"))
    assert templates

    for path in templates:
        name = path.relative_to(PROMPTS_ROOT).with_suffix("").as_posix()
        source = path.read_text(encoding="utf-8")
        placeholders = set(PLACEHOLDER_RE.findall(source))
        rendered = load_prompt(
            name,
            **{placeholder: f"<{placeholder}>" for placeholder in placeholders},
        )

        assert rendered.strip()
        assert not PLACEHOLDER_RE.search(rendered)


def test_review_stages_are_complete_prompts_without_instruction_fragments() -> None:
    review_files = {path.name for path in (PROMPTS_ROOT / "review").glob("*.md")}

    assert {"planner.md", "draft.md", "critique.md", "revision.md"} <= review_files
    assert not {
        "parallel_budget.md",
        "week_trajectory.md",
        "month_trajectory.md",
        "week_visual.md",
        "month_visual.md",
        "draft_phase.md",
        "critique_phase.md",
        "revision_phase.md",
    } & review_files

    for phase in ("draft", "critique", "revision"):
        source = (PROMPTS_ROOT / "review" / f"{phase}.md").read_text(
            encoding="utf-8"
        )
        assert set(PLACEHOLDER_RE.findall(source)) == {"output_language"}
        assert "For a weekly review" in source
        assert "For a monthly review" in source


def test_loader_preserves_non_placeholder_json_braces() -> None:
    prompt = load_prompt(
        "toc/system",
        output_language="English",
        max_tags=3,
        tag_list="- work: Work",
    )

    assert '{"summary": "...", "tags": ["tag1", "tag2"]}' in prompt


@pytest.mark.parametrize("name", ("../secret", "/absolute", "review//draft"))
def test_loader_rejects_unsafe_prompt_names(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid prompt name"):
        load_prompt(name)


def test_loader_requires_exact_placeholder_values() -> None:
    with pytest.raises(ValueError, match="Missing placeholders"):
        load_prompt("enrichment/note_system")
    with pytest.raises(ValueError, match="Unexpected placeholders"):
        load_prompt(
            "voice/transcription",
            unexpected="value",
        )
