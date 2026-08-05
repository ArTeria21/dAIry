from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReviewParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    evidence_refs: list[str]


class ReviewSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    paragraphs: list[ReviewParagraph]
    telegram_caption: str
    reflection_question: str
    safety_note: str | None = None
    visual_brief: str

    @property
    def web_text(self) -> str:
        return "\n\n".join(paragraph.text for paragraph in self.paragraphs)


class ReviewCritique(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    issues: list[str] = Field(default_factory=list)
