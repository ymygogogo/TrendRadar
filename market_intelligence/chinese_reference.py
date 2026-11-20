"""Identify headlines that benefit from a Chinese reading reference."""

import re

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]")
KANA_OR_HANGUL = re.compile(r"[\u3041-\u30ff\u3131-\u318e\uac00-\ud7a3\uff66-\uff9f]")


def needs_chinese_reference(title):
    return bool(KANA_OR_HANGUL.search(title)) or (
        not HAN.search(title) and any(character.isalpha() for character in title)
    )


def chinese_headline(value):
    if not isinstance(value, str) or not HAN.search(value) or needs_chinese_reference(value):
        return ""
    headline = " ".join(value.split())
    return headline if len(headline) <= 220 else ""


def translatable_headline(title):
    return not title.rstrip().endswith(("...", "…")) and needs_chinese_reference(title)


def headline_from_source(title, summary):
    # Titleless RSS posts get a 20-character fallback from the parser.
    if len(title) != 23 or not title.endswith("...") or not summary:
        return title
    text = " ".join(summary.split())
    if not text.startswith(title[:-3]):
        return title
    if 23 < len(text) <= 220 and not text.endswith(("...", "…")):
        return text
    sentence = re.match(r".{21,219}?[.!?](?=\s|$)", text)
    return sentence[0] if sentence else title
