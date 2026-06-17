from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Mood(str, Enum):
    """Single categorical mood label."""

    joy = "joy"
    calm = "calm"
    sadness = "sadness"
    anger = "anger"
    fear = "fear"
    neutral = "neutral"
    mixed = "mixed"


class Topic(str, Enum):
    """Journal topic taxonomy shared by note and day enrichment."""

    work = "work"
    learning = "learning"
    money = "money"
    health = "health"
    fitness = "fitness"
    nutrition = "nutrition"
    relationships = "relationships"
    travel = "travel"
    creativity = "creativity"
    identity = "identity"
    spirituality = "spirituality"
    decision_making = "decision_making"
    gratitude = "gratitude"
    technology = "technology"
    entertainment = "entertainment"
    therapy = "therapy"
    planning = "planning"
    productivity = "productivity"
    nature = "nature"
    language = "language"
    living_situation = "living_situation"
    bureaucracy = "bureaucracy"
    reflection = "reflection"


class NoteEnrichment(BaseModel):
    """One note enrichment. Field order follows the SGR cascade."""

    gist: str = Field(
        description="One neutral sentence capturing what this note is about."
    )
    mood_evidence: str = Field(
        description="Brief: which words/tone signal the note's emotional state."
    )
    mood: Mood = Field(
        description="Single best mood label, justified by mood_evidence above."
    )
    mood_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How much emotional signal the text carries. Dry logistical note -> low; "
            "raw emotional one -> high."
        ),
    )
    topics: list[Topic] = Field(
        max_length=4,
        description="What the note is about. Only genuinely-present topics, max 4.",
    )


class DayEnrichment(BaseModel):
    """Whole-day enrichment. Sparse facts are null when not explicitly mentioned."""

    summary: str = Field(
        description="2-4 sentence distilled, slightly abstracted recap of the day."
    )
    mood: Mood = Field(description="Overall mood of the day as a whole.")
    mood_confidence: float = Field(ge=0.0, le=1.0)
    key_topics: list[Topic] = Field(max_length=5)

    sport_evidence: str | None = Field(
        default=None,
        description="Quote/paraphrase if sport/exercise is mentioned, else null.",
    )
    sport: bool | None = Field(
        default=None, description="Did physical exercise/sport. null if not mentioned."
    )

    reading_evidence: str | None = Field(
        default=None,
        description="Quote/paraphrase if reading is mentioned, else null.",
    )
    reading: bool | None = Field(
        default=None, description="Read a book / read before sleep. null if not mentioned."
    )

    purchases_evidence: str | None = Field(
        default=None,
        description="Quote/paraphrase if notable/discretionary spending is mentioned, else null.",
    )
    purchases: bool | None = Field(
        default=None,
        description="Made notable discretionary purchases. null if not mentioned.",
    )

    eating_outside_evidence: str | None = Field(
        default=None,
        description="Quote/paraphrase if eating out is mentioned, else null.",
    )
    eating_outside: bool | None = Field(
        default=None,
        description="Ate out / restaurant / takeaway. null if not mentioned.",
    )

    deep_focus_evidence: str | None = Field(
        default=None,
        description="Quote/paraphrase if a stretch of deep focused work is mentioned, else null.",
    )
    deep_focus: bool | None = Field(
        default=None,
        description="Had a meaningful block of deep, focused work. null if not mentioned.",
    )

    sleep_quality_evidence: str | None = Field(
        default=None,
        description="Quote/paraphrase if sleep quality is mentioned, else null.",
    )
    sleep_quality: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Self-described sleep quality 1 (terrible) - 5 (great). null if not mentioned.",
    )
