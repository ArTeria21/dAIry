from dairy_bot.services.ai_service import _decode_message_content
from dairy_bot.texts.messages import format_transcription_preview


def test_voice_llm_string_output_is_preserved_exactly():
    raw = "  Первая строка.\nВторая <строка>!?  "

    assert _decode_message_content(raw) == raw


def test_voice_llm_multipart_output_is_concatenated_without_cleanup():
    content = [
        {"type": "text", "text": "  Первая"},
        {"type": "image_url", "image_url": {"url": "ignored"}},
        {"type": "text", "text": "\nвторая  "},
    ]

    assert _decode_message_content(content) == "  Первая\nвторая  "


def test_voice_preview_only_html_escapes_llm_output():
    raw = "  Первая <строка>\nвторая & третья  "

    preview = format_transcription_preview(raw, "ru")

    assert (
        preview
        == "<b>Предпросмотр голосовой заметки</b>\n"
        "<blockquote>  Первая &lt;строка&gt;\n"
        "вторая &amp; третья  </blockquote>\n"
        "Сохранить в сегодняшнем журнале?"
    )
