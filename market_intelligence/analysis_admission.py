"""Select the captured articles that are eligible for model analysis."""

import re
import unicodedata

from .crawl_filter import blocked_titles


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class AnalysisAdmission:
    def __init__(self, settings):
        config = settings.get("event_analysis", {}).get("admission", {})
        self.enabled = bool(config.get("enabled"))
        self.blocked_title = blocked_titles(settings)
        self.record_only_sources = set(config.get("record_only_sources", []))
        self.always_analyze_categories = set(config.get("always_analyze_categories", []))
        terms = [normalize(term) for term in config.get("media_terms", []) if term.strip()]
        patterns = [
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])" if term.isascii() else re.escape(term)
            for term in terms
        ]
        self.topic_pattern = re.compile("|".join(patterns)) if patterns else None

    def state(self, source, category, title, summary):
        if not self.enabled:
            return "pending"
        if source in self.record_only_sources or self.blocked_title(title):
            return "filtered"
        if category in self.always_analyze_categories:
            return "pending"
        text = normalize(title + " " + (summary or "")[:400])
        if self.topic_pattern is None or self.topic_pattern.search(text):
            return "pending"
        return "filtered"
