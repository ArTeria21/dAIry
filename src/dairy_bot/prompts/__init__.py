from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files


_PROMPT_NAME_RE = re.compile(r"^[a-z0-9_]+(?:/[a-z0-9_]+)*$")
_PLACEHOLDER_RE = re.compile(r"{([a-z][a-z0-9_]*)}")


@lru_cache(maxsize=None)
def _load_template(name: str) -> str:
    if not _PROMPT_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid prompt name: {name!r}")
    return (
        files(__package__)
        .joinpath(f"{name}.md")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )


def load_prompt(name: str, /, **values: object) -> str:
    """Load one Markdown prompt and replace its named placeholders."""
    template = _load_template(name)
    placeholders = set(_PLACEHOLDER_RE.findall(template))
    missing = placeholders - values.keys()
    unexpected = values.keys() - placeholders
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing placeholders for prompt {name!r}: {names}")
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ValueError(f"Unexpected placeholders for prompt {name!r}: {names}")
    return _PLACEHOLDER_RE.sub(lambda match: str(values[match.group(1)]), template)


def load_prompt_bytes(name: str) -> bytes:
    """Load a placeholder-free Markdown prompt as UTF-8 bytes."""
    return load_prompt(name).encode("utf-8")
