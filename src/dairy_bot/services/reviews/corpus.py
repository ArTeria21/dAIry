"""Compatibility surface for the diary-only review corpus."""

from dairy_bot.services.diary_corpus import scan_diary_corpus

scan_corpus = scan_diary_corpus

__all__ = ["scan_corpus"]
