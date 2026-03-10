import random
import re
from difflib import SequenceMatcher
from typing import Any

from openai import AsyncOpenAI

from dairy_bot.config import Settings

MAX_QUESTION_LEN = 280
MAX_NOTE_CONTEXT_LEN = 1400
INCLUDE_NOTE_PROBABILITY = 0.4
SIMILARITY_THRESHOLD = 0.86

def _build_system_prompt(language: str) -> str:
    return f"""You are an expert personal coach, philosopher, and mindfulness guide. Your goal is to generate exactly ONE deep, thought-provoking, open-ended question for the user's daily journal reflection.

CORE PHILOSOPHIES & SOURCES OF INSPIRATION:
1. Stoicism (e.g., "The Daily Stoic" by Ryan Holiday, Meditations by Marcus Aurelius):
   - Techniques: Dichotomy of control (focusing only on what is within one's control), Amor Fati (loving one's fate), premeditatio malorum (negative visualization), observing events objectively without value judgments.
2. Cognitive Behavioral Therapy (CBT) & Psychology:
   - Techniques: Cognitive reframing, identifying cognitive distortions (e.g., catastrophizing, black-and-white thinking, fortune-telling), separating facts from emotional narratives, exploring alternative perspectives.
3. Secular Buddhism & Applied Mindfulness (e.g., Thich Nhat Hanh, Jon Kabat-Zinn):
   - Techniques: Recognizing impermanence (Anicca), non-attachment, observing thoughts and emotions without judgment, Radical Acceptance, returning to the present moment.

GUIDELINES FOR GENERATING THE QUESTION:
- MUST be open-ended (cannot be answered with a simple "yes" or "no").
- MUST be concise, natural, and highly accessible. Avoid academic, clinical, or overly esoteric jargon.
- DO NOT lecture, moralize, diagnose, or give unsolicited advice. Only ask the question.
- The question should feel like a gentle but profound inquiry that prompts an "Aha!" moment, a shift in perspective, or deep self-honesty.
- Formulate the question in the first person ("I", "my") if it helps the user directly adopt it, or in the second person ("you", "your") as a coach speaking to them. First person is often preferred for journaling.

EXAMPLES OF EXCELLENT, GROUNDED QUESTIONS:
- "What did I avoid doing today just because it felt slightly uncomfortable?"
- "If a good friend was in my exact situation right now, what simple advice would I give them?"
- "What is one thing I complained about today that I actually have the power to change or fix?"
- "Did I actually listen to people today, or was I just waiting for my turn to speak?"
- "Which of my tasks today was actually moving me forward, and which was just busywork or distraction?"
- "What small, ordinary thing brought me a bit of joy today that I almost failed to notice?"
- "Was there a moment today where I got angry or annoyed over something that won't matter at all in a week?"
- "Am I trying to control something right now that is completely up to other people?"

OUTPUT FORMAT:
- Output ONLY the question itself. Absolutely no introductory text, no explanations, no bullet points, no quotes.
- The output language MUST be exactly: {language.upper()}.
"""


def _decode_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
        return " ".join(part for part in parts if part).strip()
    return ""


def _normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _looks_like_duplicate(candidate: str, recent_questions: list[str]) -> bool:
    normalized_candidate = _normalize_question(candidate)
    for question in recent_questions:
        normalized_question = _normalize_question(question)
        if normalized_candidate == normalized_question:
            return True
        similarity = SequenceMatcher(
            None, normalized_candidate, normalized_question
        ).ratio()
        if similarity >= SIMILARITY_THRESHOLD:
            return True
    return False


def _sanitize_question(raw_text: str) -> str:
    cleaned = raw_text.strip().strip('"').strip("'")
    cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned and not cleaned.endswith("?"):
        cleaned = f"{cleaned}?"
    return cleaned


def _build_user_prompt(recent_questions: list[str], note_context: str | None, language: str) -> str:
    history_block = "\n".join(f"- {question}" for question in recent_questions[-15:])
    if not history_block:
        history_block = "- (no previous deep questions)"

    note_block = "No journal note context in this run."
    if note_context:
        snippet = note_context.strip()
        if len(snippet) > MAX_NOTE_CONTEXT_LEN:
            snippet = snippet[:MAX_NOTE_CONTEXT_LEN].rstrip() + "..."
        note_block = f"Journal note context (use only as optional inspiration):\n{snippet}"

    return (
        f"Task: Generate exactly one reflective question in {language.upper()}.\n"
        "Hard constraints:\n"
        "- One question only, no preface, no bullets, no explanation.\n"
        "- Open-ended question with no single correct answer.\n"
        "- Suitable for journaling and personal life reflection.\n"
        "- Keep it concise (max 35 words).\n"
        "- Avoid repeating the themes and wording from recent questions.\n\n"
        f"Recent questions to avoid repeating:\n{history_block}\n\n"
        f"{note_block}"
    )


async def _request_question(
    client: AsyncOpenAI,
    model_name: str,
    language: str,
    recent_questions: list[str],
    note_context: str | None,
) -> str:
    completion = await client.chat.completions.create(
        model=model_name,
        temperature=1.0,
        messages=[
            {"role": "system", "content": _build_system_prompt(language)},
            {
                "role": "user",
                "content": _build_user_prompt(recent_questions, note_context, language),
            },
        ],
    )
    return _sanitize_question(_decode_message_content(completion.choices[0].message.content))


async def generate_deep_question(
    settings: Settings,
    recent_questions: list[str],
    random_note_text: str | None,
) -> str:
    include_note_context = bool(
        random_note_text and random.random() < INCLUDE_NOTE_PROBABILITY
    )
    note_context = random_note_text if include_note_context else None

    client = AsyncOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
    )
    try:
        for _ in range(3):
            question = await _request_question(
                client=client,
                model_name=settings.question_model_name,
                language=settings.question_language,
                recent_questions=recent_questions,
                note_context=note_context,
            )
            if not question:
                continue
            if len(question) > MAX_QUESTION_LEN:
                continue
            if _looks_like_duplicate(question, recent_questions):
                continue
            return question
    except Exception as exc:  # pragma: no cover - best-effort guard
        raise RuntimeError("Deep question generation failed") from exc
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    raise RuntimeError("Deep question generation failed validation")
