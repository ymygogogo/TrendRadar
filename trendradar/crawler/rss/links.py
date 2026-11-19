"""Read published article links from a configured official index page."""

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from .parser import ParsedRSSItem


class ArticleLinks(HTMLParser):
    def __init__(self, index_url, article_pattern, title_pattern=''):
        super().__init__(convert_charrefs=True)
        self.index_url = index_url
        self.hostname = urlsplit(index_url).hostname
        self.pattern = re.compile(article_pattern)
        self.title_pattern = re.compile(title_pattern) if title_pattern else None
        self.link = None
        self.fragments = []
        self.entries = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        self.link = None
        self.fragments = []
        href = dict(attrs).get("href")
        if not href:
            return
        url = urljoin(self.index_url, href)
        parsed = urlsplit(url)
        if (parsed.scheme in {"http", "https"} and parsed.hostname == self.hostname
                and self.pattern.search(parsed.path)):
            self.link = parsed._replace(scheme="https", fragment="").geturl()

    def handle_data(self, data):
        if self.link:
            self.fragments.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or not self.link:
            return
        title = " ".join("".join(self.fragments).split())
        if (len(title) >= 5 and self.link not in self.entries
                and (self.title_pattern is None or self.title_pattern.search(title))):
            # A date in a URL has day precision. Never manufacture a publication hour.
            day = re.search(r"/t(20\d{6})_", self.link)
            summary = ""
            if day:
                try:
                    date = datetime.strptime(day[1], "%Y%m%d").date()
                    summary = f"原文链接日期（北京时间，时分未知）: {date.isoformat()}"
                except ValueError:
                    pass
            self.entries[self.link] = ParsedRSSItem(title=title, url=self.link, summary=summary)
        self.link = None


def parse_article_index(html, url, pattern, title_pattern=""):
    parser = ArticleLinks(url, pattern, title_pattern)
    parser.feed(html)
    if not parser.entries and not title_pattern:
        raise ValueError("official index returned no matching article links")
    return list(parser.entries.values())


def parse_official_qa(html):
    """Extract a public spokesperson question and its answer, if both are present."""
    text = " ".join(unescape(re.sub(r"<[^>]*>", " ", html)).split())
    match = re.search(r"问[：:]\s*(.{8,460}?)\s*答[：:]\s*(.{5,600}?)(?:ICP备案|版权所有|打印|$)", text)
    if not match:
        match = re.search(r"问[：:]\s*(.{8,460}?)\s*答[：:]\s*(.{5,600})", text)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()
