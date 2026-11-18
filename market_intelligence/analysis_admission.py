"""Select the captured articles that are eligible for model analysis."""

from .crawl_filter import blocked_titles
from .news_topics import NewsTopics, normalize, term_pattern


class AnalysisAdmission:
    def __init__(self, settings):
        config = settings.get("event_analysis", {}).get("admission", {})
        self.enabled = bool(config.get("enabled"))
        self.news_topics = NewsTopics(settings) if config.get("include_news_topics") else None
        self.blocked_title = blocked_titles(settings)
        self.record_only_sources = set(config.get("record_only_sources", []))
        self.always_analyze_categories = set(config.get("always_analyze_categories", []))
        self.topic_pattern = term_pattern(config.get("media_terms", []))

    def state(self, source, category, title, summary):
        if not self.enabled:
            return "pending"
        if source in self.record_only_sources or self.blocked_title(title):
            return "filtered"
        if self.news_topics and self.news_topics.excludes(title):
            return "filtered"
        if category in self.always_analyze_categories:
            return "pending"
        text = normalize(title + " " + (summary or "")[:400])
        if self.topic_pattern is None or self.topic_pattern.search(text) or (
            self.news_topics and self.news_topics.matches_topic(text)
        ):
            return "pending"
        return "filtered"
