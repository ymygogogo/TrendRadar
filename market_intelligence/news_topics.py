"""Editorial topics used by candidate admission and the news digest."""

import re
import unicodedata


TOPIC_LABELS = {"tech": "科技", "economy": "经济", "world": "全球"}
DEFAULT_TOPICS = {
    "tech": (
        "科技", "技术突破", "芯片", "半导体", "算力", "人工智能", "英伟达", "数据中心", "网络攻击", "网络安全",
        "机器人", "航天", "量子", "technology", "tech", "semiconductor", "semiconductors", "chip", "chips",
        "artificial intelligence", "ai", "nvidia", "data center", "cyberattack", "cybersecurity", "spaceflight",
    ),
    "economy": (
        "经济", "宏观", "货币政策", "财政", "贸易", "关税", "金融", "央行", "银行", "通胀", "固定资产投资",
        "能源", "原油", "油气", "铜矿", "产业链", "economy", "economic", "finance", "financial", "tariff",
        "trade", "inflation", "central bank", "federal reserve", "oil", "energy", "monetary",
    ),
    "world": (
        "国际", "全球", "外交", "峰会", "大选", "制裁", "停火", "空袭", "冲突", "战争", "地震", "海啸", "飓风",
        "国际政治", "地缘政治", "地缘", "geopolitics", "world affairs", "diplomacy", "election", "sanctions",
        "ceasefire", "airstrikes", "war", "conflict", "earthquake", "tsunami", "hurricane",
    ),
}
DEFAULT_EXCLUSIONS = (
    "饭圈", "圈粉", "明星红毯", "粉丝应援", "明星八卦", "综艺", "追星", "偶像恋情", "影视八卦", "票房", "明星穿搭", "演唱会", "追剧", "电影节",
    "entertainment", "celebrity", "celebrity gossip", "red carpet", "reality tv", "box office", "celebrity breakup",
    "足球比赛", "英超", "国足", "英超联赛", "football highlights", "premier league", "sports results",
)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def term_pattern(terms):
    patterns = [
        rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])" if term.isascii() else re.escape(term)
        for term in (normalize(value) for value in terms if isinstance(value, str) and value.strip())
    ]
    return re.compile("|".join(patterns)) if patterns else None


class NewsTopics:
    def __init__(self, settings):
        config = settings.get("digest", {})
        configured = config.get("topic_terms", {})
        self.patterns = {name: term_pattern(configured.get(name, terms)) for name, terms in DEFAULT_TOPICS.items()}
        self.excluded = term_pattern(config.get("excluded_topic_terms", DEFAULT_EXCLUSIONS))

    def matches_topic(self, value):
        text = normalize(value)
        return next((name for name, pattern in self.patterns.items() if pattern and pattern.search(text)), None)

    def excludes(self, value):
        return bool(self.excluded and self.excluded.search(normalize(value)))

    def classify(self, title, categories, summary=""):
        if self.excludes(title) or any(self.excludes(category) for category in categories):
            return None
        return (self.matches_topic(title)
                or next((topic for category in categories if (topic := self.matches_topic(category))), None)
                or self.matches_topic(summary[:400]))
