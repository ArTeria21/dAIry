from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .models import CorpusDocument


@dataclass(frozen=True, slots=True)
class EmbeddedDocument:
    document: CorpusDocument
    embedding: tuple[float, ...]
    embedding_model: str
    embedding_dimension: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    document: CorpusDocument
    score: float


def search_corpus(
    query_embedding: Sequence[float],
    documents: Iterable[EmbeddedDocument],
    *,
    cutoff: date,
    embedding_model: str,
    limit: int = 10,
) -> list[SearchHit]:
    """Search a historical corpus while preferring distinct dates and sources."""
    query = tuple(float(value) for value in query_embedding)
    if not query or limit <= 0:
        return []

    candidates: list[SearchHit] = []
    for item in documents:
        if item.document.source_type != "diary":
            continue
        if not _compatible(item, query, embedding_model, cutoff):
            continue
        score = _cosine(query, item.embedding)
        if score is None:
            continue
        candidates.append(SearchHit(document=item.document, score=score))

    candidates.sort(key=lambda hit: (-hit.score, hit.document.document_id))
    grouped: dict[str, list[SearchHit]] = defaultdict(list)
    group_order: list[str] = []
    for hit in candidates:
        key = _diversity_key(hit.document)
        if key not in grouped:
            group_order.append(key)
        grouped[key].append(hit)

    diversified: list[SearchHit] = []
    depth = 0
    while len(diversified) < limit:
        added = False
        for key in group_order:
            group = grouped[key]
            if depth < len(group):
                diversified.append(group[depth])
                added = True
                if len(diversified) == limit:
                    break
        if not added:
            break
        depth += 1
    return diversified


def _compatible(
    item: EmbeddedDocument,
    query: tuple[float, ...],
    embedding_model: str,
    cutoff: date,
) -> bool:
    return (
        item.document.eligible_on(cutoff)
        and item.embedding_model == embedding_model
        and item.embedding_dimension == len(query)
        and len(item.embedding) == len(query)
        and item.content_hash == item.document.content_hash
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    score = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return max(-1.0, min(1.0, score))


def _diversity_key(document: CorpusDocument) -> str:
    if document.source_type == "diary" and document.document_date is not None:
        return f"diary:{document.document_date.isoformat()}"
    return f"{document.source_type}:{document.path}"
