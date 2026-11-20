"""Publish only the static, public projection of TrendRadar's news and digests."""

from base64 import b64encode
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import fcntl
import json
from hashlib import sha256
from html import escape
from ipaddress import ip_address
import os
from pathlib import Path
import re
from unicodedata import normalize
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

import requests

from trendradar.report.rss_views import rss_groups

from .chinese_reference import chinese_headline, headline_from_source, translatable_headline
from .crawl_filter import blocked_titles
from .digests import DIRECTIONS_ZH, REGION_LABELS, digest_url, region_summary, safe_url
from .news_topics import NewsTopics, TOPIC_LABELS
from .ui_theme import THEME


def public_origin():
    value = os.environ.get("MARKET_PUBLIC_NEWS_URL", "").rstrip("/")
    if value and not re.fullmatch(r"https://[a-z0-9.-]+", value):
        raise ValueError("MARKET_PUBLIC_NEWS_URL must be a bare HTTPS origin")
    if bool(value) != bool(os.environ.get("MARKET_PUBLIC_NEWS_BUCKET")):
        raise ValueError("Set both MARKET_PUBLIC_NEWS_URL and MARKET_PUBLIC_NEWS_BUCKET")
    return value


def external_url(value):
    value = safe_url(value)
    if not value:
        return ""
    parts = urlsplit(value)
    host = parts.hostname
    if host == urlsplit(os.environ.get("MARKET_DIGEST_BASE_URL", "")).hostname:
        return ""
    if "." not in host or host.endswith((".lan", ".local", ".internal", ".home.arpa")):
        return ""
    try:
        if not ip_address(host).is_global:
            return ""
    except ValueError:
        pass
    query = urlencode([(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                       if not re.search(r"key|token|signature|credential|secret|password|^sig$", key, re.I)])
    return parts._replace(query=query, fragment="").geturl()


def public_edition(edition):
    return {
        "id": edition["id"], "title": edition["title"], "created_at": edition["created_at"],
        "target": edition["target"], "closing": edition.get("closing", []),
        "market_context": [{**item, "url": url} for item in edition.get("market_context", [])
                           if (url := external_url(item["url"]))],
        "events": [
            {**{key: event[key] for key in (
                "title", "summary", "topic", "tier", "heat", "priority", "score", "verification",
                "reported_at", "direction", "impact", "outlet_count", "hotlist_rank", "edition_region",
            ) if key in event},
             "source": external_url(event["source"]),
             "sources": [{**{key: item[key] for key in (
                              "name", "international", "domestic", "medium", "youtube_views") if key in item},
                          "url": url}
                         for item in event.get("sources", []) if (url := external_url(item["url"]))],
             "other_sources": [url for source in event.get("other_sources", []) if (url := external_url(source))],
             "concepts": event.get("concepts", []),
             "sectors": event.get("sectors", []),
             "companies": [{**company, "source": external_url(company["source"])}
                           for company in event.get("companies", []) if external_url(company["source"])]}
            for event in edition["events"]
        ],
    }


SITE_STYLE = THEME + """
:root { color:var(--ink); background:var(--bg); font:1rem/1.6 var(--font); }
body { margin:0; }
main,.masthead { width:min(100% - 2.5rem,73rem); margin-inline:auto; }
header { border-bottom:1px solid var(--edge-soft); background:var(--header); }
.masthead { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem 1rem; min-height:3.65rem; padding:.4rem 0; }
nav,.actions,.scope,.controls { display:flex; flex-wrap:wrap; gap:.45rem .6rem; align-items:center; }
nav a { color:var(--muted); text-decoration:none; padding:.4rem .7rem; border-radius:.38rem; }
nav a:hover,nav a[aria-current=page] { color:var(--accent); background:var(--accent-quiet); }
main { padding:1.7rem 0 3rem; }
a { color:var(--accent); overflow-wrap:anywhere; text-underline-offset:.22em; }
h1,h2,p { margin:0; }
h1 { font-size:clamp(1.65rem,4.2vw,2.5rem); line-height:1.15; letter-spacing:-.035em; }
h2 { font-size:1.16rem; line-height:1.4; }
small,.muted { color:var(--muted); font-size:.85rem; }
#collected { font-size:.81rem; letter-spacing:.015em; font-variant-numeric:tabular-nums; }
#collected time { color:#b8edf7; font-weight:680; }
.news-hero {
  isolation:isolate; position:relative; overflow:hidden; display:flex; align-items:center;
  min-height:20rem; border:1px solid #34586f; border-radius:1rem;
  background:#091723; box-shadow:inset 0 1px 0 #8ae8fa28,0 15px 54px #0006;
}
.radar-visual {
  position:absolute; inset:0 0 0 auto; width:76%; height:100%; object-fit:cover; object-position:center 51%;
  z-index:-2;
}
.news-hero::before {
  content:""; position:absolute; inset:0; z-index:-1; pointer-events:none;
  background:linear-gradient(90deg,#091723 23%,#091723ed 34%,#09172365 61%,transparent 86%);
}
.radar-sweep {
  position:absolute; z-index:-2; pointer-events:none; inset-inline-end:4%; top:50%;
  width:clamp(20rem,58vw,44rem); aspect-ratio:1; translate:0 -50%;
  mask-image:radial-gradient(circle,black 10%,#000c 42%,transparent 68%);
}
.radar-sweep::before {
  content:""; position:absolute; inset:0;
  background:conic-gradient(from 0deg,transparent 0 304deg,#62ecff00 315deg,#62ecff52 357deg,#a6fbff91 359deg,transparent 360deg);
  animation:radar-scan 3s cubic-bezier(.28,.27,.52,1) both;
}
@keyframes radar-scan {
  0% { transform:rotate(-95deg); opacity:0; }
  17% { opacity:.95; }
  76% { opacity:.85; }
  100% { transform:rotate(225deg); opacity:0; }
}
.news-hero::after {
  content:""; position:absolute; inset:auto 0 0; height:1px; pointer-events:none;
  background:linear-gradient(90deg,#69dae8,#488aa52b 74%,transparent);
}
.brief-main { display:grid; gap:.5rem; padding:clamp(1.2rem,3.5vw,2.8rem); max-width:62%; min-width:0; }
.brief-main h1 { font-size:clamp(2.2rem,5.6vw,4.55rem); letter-spacing:-.045em; margin:.15rem 0 .34rem; }
.brief-main h2 { font-size:clamp(1.02rem,2.2vw,1.55rem); }
.brief-main h2 a { color:var(--ink); text-decoration:none; }
.brief-main h2 a:hover { text-decoration:underline; }
.digest-meta { font-size:.85rem; color:#a7c8da; }
.brief-outlook { margin-top:1.35rem; }
.outlook-heading { display:flex; align-items:baseline; justify-content:space-between; gap:.4rem .8rem; flex-wrap:wrap; }
.outlook-heading h2 { font-size:1.24rem; }
.brief-headlines { margin:.65rem 0 0; padding:0; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.65rem; list-style:none; }
.brief-headlines li { min-width:0; border:1px solid var(--edge); border-top:2px solid #3d657b;
  border-radius:var(--radius); background:var(--surface); padding:.8rem .95rem; }
.brief-headlines h3 { font-size:1rem; margin:.16rem 0 .45rem; line-height:1.45; overflow-wrap:anywhere; }
.brief-headlines h3 a { color:var(--ink); text-decoration:none; }
.brief-headlines h3 a:hover { text-decoration:underline; }
.brief-headlines small { display:block; color:var(--accent); font-weight:600; }
.brief-headlines dl { margin:0; display:grid; gap:.3rem; }
.brief-headlines dt { color:var(--muted); font-size:.76rem; font-weight:620; }
.brief-headlines dd { margin:0; color:var(--ink); font-size:.86rem; line-height:1.48; overflow-wrap:anywhere; }
.outlook-research { border-top:1px solid var(--edge-soft); padding-top:.43rem; margin-top:.15rem; }
.outlook-research dt { color:#abdeeb; }
.brief-headlines .outlook-watch { color:var(--muted); }
.brief-main .actions { margin-top:.5rem; }
.topic-card,.history { border:1px solid var(--edge); border-radius:var(--radius); background:var(--surface); }
.button-link { display:inline-flex; align-items:center; padding:.45rem .8rem; min-height:2.65rem; border-radius:.45rem;
  background:var(--accent); color:var(--accent-ink); font-weight:720; text-decoration:none; }
.news-heading { margin-top:1.85rem; font-size:1.25rem; }
.scope button { font-variant-numeric:tabular-nums; }
.controls,.scope { margin:.65rem 0; }
.controls input { flex:1; min-width:min(100%,14rem); }
button,input,select { color:inherit; background:var(--surface); font:inherit; border:1px solid var(--edge);
  border-radius:.45rem; padding:.42rem .7rem; min-height:2.6rem; }
input::placeholder { color:var(--muted); }
button, summary { cursor:pointer; }
button:hover, select:hover, input:hover { border-color:var(--muted); }
button[aria-pressed=true] { border-color:var(--accent); background:var(--accent-quiet); color:var(--accent); font-weight:690; }
.export-menu { position:relative; }
.export-menu summary { padding:.4rem .55rem; color:var(--muted); }
.export-menu .actions { position:absolute; inset-inline-end:0; z-index:2; width:max-content; max-width:85vw;
  padding:.5rem; background:var(--header); border:1px solid var(--edge); border-radius:.55rem; }
.news-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.8rem; align-items:start; margin-top:.55rem; }
.news-grid:not([hidden]) { animation:news-enter .27s ease-out both; }
@keyframes news-enter { from { opacity:.6; transform:translateY(.28rem); } }
.topic-card { padding:.85rem 1rem; min-width:0; }
.topic-card h3 { font-size:1rem; margin:0; display:flex; align-items:baseline; justify-content:space-between; gap:.5rem; }
.topic-card h3 small { font-variant-numeric:tabular-nums; }
.topic-card ul,.history { padding:0; margin:.5rem 0 0; list-style:none; }
.topic-card li,.history li { padding:.69rem 0; border-top:1px solid var(--edge-soft); }
.topic-card li:last-child { padding-bottom:0; }
.topic-card li a { display:block; color:var(--ink); text-decoration:none; font-weight:620; }
.topic-card li small { display:block; margin-top:.1rem; }
.topic-card li .news-title:hover { text-decoration:underline; text-underline-offset:.18em; }
.news-reference { color:#c8eaf4; font-size:.93rem; line-height:1.5; margin:.24rem 0 .32rem; }
.news-reference .reference-label { color:var(--muted); font-size:.73rem; margin-inline-end:.4rem; white-space:nowrap; }
.news-source { margin-inline-end:.15rem; color:var(--accent); font-weight:580; }
.history li { padding:.8rem 1rem; }
.history li:first-child { border:0; }
#result-summary { margin:.55rem 0; }
[hidden] { display:none!important; }
@media (max-width:44rem) {
  main,.masthead { width:calc(100% - 2rem); }
  .news-grid,.brief-headlines { grid-template-columns:1fr; }
  .news-hero { min-height:15.35rem; }
  .brief-main { max-width:100%; width:100%; }
  .radar-sweep { inset-inline-end:-3.75rem; }
  .radar-visual { width:100%; object-position:60% 50%; }
  .news-hero::before { background:linear-gradient(90deg,#091723f5 3%,#091723ba 52%,#09172333 100%); }
  .brief-headlines { gap:.48rem; }
  .brief-headlines li { padding:.68rem .75rem; }
  .brief-headlines h3 { font-size:.95rem; }
  .scope { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
  .scope button { text-align:start; }
  header nav { gap:.15rem; }
  header nav a { padding:.35rem .42rem; font-size:.88rem; }
  main { padding-top:1.15rem; }
  .controls input { flex-basis:100%; }
  .export-menu .actions { position:static; width:auto; }
}
@media (prefers-reduced-motion:reduce) {
  .radar-sweep::before,.news-grid:not([hidden]) { animation:none; }
  .radar-sweep { display:none; }
}
@media print {
  :root { color-scheme:light; --ink:#111; --muted:#333; --edge:#ddd; --edge-soft:#e5e5e5; --surface:#fff; --bg:#fff; --accent:#111; }
  header,.scope,.controls,.radar-visual,.radar-sweep,.news-hero::before,.news-hero::after { display:none; }
  .news-hero { min-height:0; background:var(--bg); box-shadow:none; }
  #collected time,.digest-meta { color:var(--muted); }
  .news-grid:not([hidden]) { animation:none; }
  .news-reference { color:var(--ink); }
  .topic-card li { break-inside:avoid; }
}
"""
# The landing page is published as one self-contained OSS object.
HERO_ASSET = Path(__file__).with_name("static") / "global-radar.webp"


@lru_cache(maxsize=1)
def _hero_image():
    try:
        data = b64encode(HERO_ASSET.read_bytes()).decode("ascii")
    except FileNotFoundError:
        return ""
    return f'<img class="radar-visual" alt="" src="data:image/webp;base64,{data}" fetchpriority="high" decoding="async">'


def _page(title, body, script="", *, history=False, heading=True):
    current = ' aria-current="page"'
    page_heading = f"<h1>{title}</h1>" if heading else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · TrendRadar</title><style>{SITE_STYLE}</style></head>
<body><header><div class="masthead"><a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span>TrendRadar <small>新闻</small></a><nav aria-label="主导航">
<a href="/"{"" if history else current}>新闻首页</a><a href="/digests/index.html"{current if history else ""}>简报历史</a>
</nav></div></header><main>{page_heading}{body}</main>{script}</body></html>""".encode()


def latest_published_edition(db):
    row = db.execute("""SELECT e.payload FROM digest_editions e
        JOIN published_digest_editions p USING (delivery_key)
        ORDER BY julianday(e.created_at) DESC, e.delivery_key DESC LIMIT 1""").fetchone()
    return json.loads(row[0]) if row else None


def _outlook_card(event, href):
    def preview(value, limit):
        text = " ".join((value or "").split())
        return escape(text if len(text) <= limit else text[:limit].rstrip() + "…")

    label = " · ".join(filter(None, (REGION_LABELS.get(event.get("edition_region")),
                                    TOPIC_LABELS.get(event.get("topic")))))
    meta = f'<small>{escape(label)}</small>' if label else ""
    summary = preview(event.get("summary"), 145)
    fact = f'<div><dt>事件摘要</dt><dd>{summary}</dd></div>' if summary else ""
    direction = DIRECTIONS_ZH.get(event.get("direction"), "影响不明确")
    impact = preview(event.get("impact"), 155) or "暂无足够证据判断具体影响。"
    signals = [signal for signal in (event.get("follow_up_signals") or []) if "://" not in signal]
    signal = preview(signals[0], 80) if signals else ""
    watch = f'<div><dt>后续观察</dt><dd class="outlook-watch">{signal}</dd></div>' if signal else ""
    return (f'<li>{meta}<h3><a href="{href}">{escape(event["title"])}</a></h3><dl>{fact}'
            f'<div class="outlook-research"><dt>AI 研判 · {direction}</dt><dd>{impact}</dd></div>{watch}</dl></li>')


def _latest_brief(brief, now):
    collected = f'<p id="collected" class="muted">本轮采集 <time datetime="{now.isoformat()}">{now:%m-%d %H:%M}</time> · 北京时间</p>'
    heading = f'{collected}<h1>全球要闻</h1>'
    image = _hero_image() + '<span class="radar-sweep" aria-hidden="true"></span>'
    if not brief or not re.fullmatch(r"[a-f0-9]{24}", brief["id"]):
        return (f'<section class="news-hero" aria-label="新闻简报">{image}<div class="brief-main">{heading}'
                '<div class="actions"><a class="button-link" href="/digests/index.html">浏览简报历史</a></div>'
                '</div></section>')
    path = digest_url("", brief["id"], public=True)
    stamp = datetime.fromisoformat(brief["created_at"]).strftime("%m-%d %H:%M")
    events = brief["events"]
    details = " · ".join(filter(None, (stamp, f"{len(events)} 条", region_summary(events))))
    choices = {next((i for i, event in enumerate(events) if event.get("edition_region") == region), None)
               for region in ("domestic", "international")}
    choices.discard(None)
    for index in range(len(events)):
        if len(choices) == 3:
            break
        choices.add(index)
    items = "".join(_outlook_card(events[i], f"{path}#event-{i + 1}") for i in sorted(choices))
    outlook = (f'<section class="brief-outlook" aria-labelledby="outlook-title"><div class="outlook-heading">'
               f'<h2 id="outlook-title">本期 AI 要点</h2><a href="{path}">查看全部 {len(events)} 条</a></div>'
               f'<ul class="brief-headlines">{items}</ul></section>') if items else ""
    return f"""<section class="news-hero" aria-label="最新简报">{image}
<div class="brief-main">{heading}<p class="digest-meta">最新简报 · {details}</p>
<h2><a href="{path}">{escape(brief["title"])}</a></h2>
<div class="actions"><a class="button-link" href="{path}">阅读整期</a>
<a href="/digests/index.html">浏览历史</a></div></div></section>
{outlook}"""


def today_groups(db, settings, now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    topics = NewsTopics(settings)
    blocked = blocked_titles(settings)
    grouped = {topic: [] for topic in TOPIC_LABELS}
    rows = db.execute("""SELECT id,title,url,summary,published_at,feed_id,source_name FROM articles
        WHERE published_at IS NOT NULL AND julianday(published_at) BETWEEN julianday(?) AND julianday(?)
        ORDER BY julianday(published_at) DESC, id""", (start.isoformat(), now.isoformat()))
    for _, title, url, summary, published, feed, name in rows:
        topic = topics.classify(title, (), summary or "")
        if topic and not blocked(title):
            timestamp = datetime.fromisoformat(published)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            stamp = timestamp.astimezone(now.tzinfo)
            grouped[topic].append({"title": title, "summary": summary, "url": url, "source_id": feed, "source_name": name or feed,
                                   "time_display": stamp.strftime("%H:%M")})
    return [{"word": TOPIC_LABELS[topic], "titles": titles} for topic, titles in grouped.items() if titles]


def chinese_references(db, now):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    references = {}
    if "article_chinese_references" in tables:
        cutoff = (now - timedelta(days=2)).isoformat()
        rows = db.execute("""SELECT a.title,r.headline_zh FROM article_chinese_references r
            JOIN articles a ON a.id=r.article_id WHERE a.collected_at>=?
            ORDER BY a.collected_at, a.id""", (cutoff,))
        references.update((title, headline) for title, value in rows if (headline := chinese_headline(value)))
    if "public_headline_translations" in tables:
        rows = db.execute("SELECT original,headline_zh FROM public_headline_translations")
        references.update((title, headline) for title, value in rows if (headline := chinese_headline(value)))
    return references


def _rss_panel(groups, view, regions, active, references):
    output = []
    for group in groups:
        items = []
        for article in group["titles"]:
            url = external_url(article.get("url", ""))
            original = headline_from_source(article.get("title", ""), article.get("summary"))
            title = escape(original)
            if not url or not title:
                continue
            author = escape(article.get("source_name", ""))
            stamp = escape(article.get("time_display", ""))
            region = regions.get(article.get("source_id"), "")
            if region not in ("domestic", "international"):
                region = ""
            region_label = {"domestic": "国内", "international": "海外"}.get(region, "")
            badge = "新增" if article.get("is_new") and view == "all" else ""
            source_meta = f'<span class="news-source">{author}</span>' if author else ""
            rest = " · ".join(filter(None, (region_label, stamp, badge)))
            metadata = " · ".join(filter(None, (source_meta, rest)))
            reference = ""
            if translatable_headline(original) and (headline := references.get(original)):
                reference = ('<p class="news-reference" lang="zh-Hans" data-chinese-reference>'
                             f'<span class="reference-label">中文参考</span>{escape(headline)}</p>')
            items.append(f'<li data-region="{region}"><a class="news-title" href="{escape(url)}" rel="noreferrer">{title}</a>'
                         f'{reference}<small><span class="source-meta">{metadata}</span></small></li>')
        if items:
            output.append((f'<section class="topic-card"><h3>{escape(group["word"])} <small>{len(items)} 条</small></h3>'
                           f'<ul>{"".join(items)}</ul></section>', len(items)))
    hidden = "" if view == active else " hidden"
    return f'<div data-view="{view}"{hidden} class="news-grid">{"".join(html for html, _ in output)}</div>', sum(count for _, count in output)


def hotlist_groups(results, names, settings):
    topics = NewsTopics(settings)
    grouped = {topic: [] for topic in TOPIC_LABELS}
    for source, articles in results.items():
        for title, article in articles.items():
            topic = topics.classify(title, ())
            url = external_url(article.get("url") or article.get("mobileUrl", ""))
            ranks = [rank for rank in article.get("ranks", []) if type(rank) is int and rank > 0]
            if not (topic and url and ranks):
                continue
            rank = min(ranks)
            grouped[topic].append((rank, {
                "title": title, "url": url, "source_name": names.get(source, source), "source_id": source,
                "time_display": f"榜单第 {rank} 名",
            }))
    groups = []
    seen = set()
    for topic, entries in grouped.items():
        titles = []
        for _, article in sorted(entries, key=lambda entry: entry[0]):
            if article["url"] in seen:
                continue
            seen.add(article["url"])
            titles.append(article)
        groups.append({"word": TOPIC_LABELS[topic], "titles": titles})
    return groups


def _unique_home_groups(groups):
    from .engine import canonical_url

    seen_urls = set()
    seen_yahoo = set()
    output = []
    for group in groups:
        titles = []
        for article in group["titles"]:
            url = external_url(article.get("url", ""))
            if not url or not article.get("title"):
                continue
            key = canonical_url(url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            parts = urlsplit(key)
            if parts.hostname in {"tw.stock.yahoo.com", "tw.news.yahoo.com"}:
                story = re.fullmatch(r"/(?:news/)?([^/]+)-[0-9]{9}\.html", unquote(parts.path))
                if story:
                    title = " ".join(normalize("NFKC", article["title"]).casefold().split())
                    yahoo_key = (story[1].casefold(), title)
                    if yahoo_key in seen_yahoo:
                        continue
                    seen_yahoo.add(yahoo_key)
            titles.append(article)
        if titles:
            output.append({**group, "titles": titles})
    return output


def _home_views(current, new, mode, today, hotlist):
    recent, all_groups = rss_groups(current, new, mode)
    active = "today" if today else "all" if mode != "incremental" and not recent and all_groups else "recent"
    views = [("today", "今日新闻", today)] if today is not None else []
    views.append(("recent", "本轮新增", recent))
    if mode != "incremental":
        views.append(("all", "今日全部" if mode == "daily" else "当前全部", all_groups))
    views.append(("hot", "热点榜单", hotlist))
    return active, [(view, label, _unique_home_groups(groups)) for view, label, groups in views]


def foreign_headlines(current, new, mode, *, today, hotlist):
    _, views = _home_views(current, new, mode, today, hotlist)
    titles = {}
    for _, _, groups in views:
        for group in groups:
            for article in group["titles"]:
                title = headline_from_source(article.get("title", ""), article.get("summary"))
                if translatable_headline(title) and external_url(article.get("url", "")):
                    titles[title] = None
    return list(titles)


def render_home(current, new, now, mode="current", *, hotlist=(), regions=None, brief=None, today=None, references=None):
    active, views = _home_views(current, new, mode, today, hotlist)
    rendered = [(view, label, *_rss_panel(groups, view, regions or {}, active, references or {})) for view, label, groups in views]
    switch = "".join(
        f'<button type="button" data-show="{view}" aria-pressed="{str(view == active).lower()}">{label} {count}</button>'
        for view, label, _, count in rendered)
    panels = "".join(panel for _, _, panel, _ in rendered)
    initial_count = next(count for view, _, _, count in rendered if view == active)
    body = f"""{_latest_brief(brief, now)}<h2 id="browse" class="news-heading">新闻速览</h2>
<div class="scope" aria-labelledby="browse">{switch}</div>
<div class="controls">
<input type="search" aria-label="搜索原文、中文参考或来源" placeholder="搜索原文、中文参考或来源">
<select aria-label="来源地区" id="region"><option value="">全部来源</option>
<option value="domestic">国内来源</option><option value="international">海外来源</option></select>
<details class="export-menu"><summary>导出</summary><div class="actions">
<button type="button" id="export">导出 Markdown</button><button type="button" id="image">导出图片</button>
<button type="button" id="print">打印 / PDF</button></div></details></div>
<p id="export-status" role="status" hidden></p>
<p id="result-summary" class="muted" role="status">{"当前显示 " + str(initial_count) + " 条" if initial_count else "暂无新闻。"}</p>{panels}"""
    return _page("全球要闻", body, r"""<script>
const panels = [...document.querySelectorAll('[data-view]')];
const buttons = [...document.querySelectorAll('[data-show]')];
const search = document.querySelector('input[type=search]');
const region = document.querySelector('#region');
const summary = document.querySelector('#result-summary');
function filterNews() {
  const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const active = panels.find(panel => !panel.hidden);
  let count = 0;
  for (const section of active.querySelectorAll('section')) {
    let matches = 0;
    for (const entry of section.querySelectorAll('li')) {
      const searchable = [...entry.querySelectorAll('.news-title,[data-chinese-reference],.source-meta')]
        .map(element => element.textContent).join(' ').toLowerCase();
      entry.hidden = Boolean(region.value && region.value !== entry.dataset.region)
        || !terms.every(term => searchable.includes(term));
      if (!entry.hidden) matches++;
    }
    section.hidden = matches === 0;
    section.querySelector('h3 small').textContent = matches + ' 条';
    count += matches;
  }
  summary.textContent = count ? '当前显示 ' + count + ' 条'
    : terms.length || region.value ? '没有符合条件的新闻。' : '暂无新闻，可切换视图查看。';
}
for (const button of buttons) button.addEventListener('click', () => {
  for (const option of buttons) option.setAttribute('aria-pressed', String(option === button));
  for (const panel of panels) panel.hidden = panel.dataset.view !== button.dataset.show;
  filterNews();
});
search.addEventListener('input', filterNews);
region.addEventListener('change', filterNews);
document.querySelector('#print').addEventListener('click', () => window.print());
const imageButton = document.querySelector('#image');
let imageLibrary;
function loadImageLibrary() {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
    script.integrity = 'sha512-BNaRQnYJYiPSqHHDb58B0yaPfCu+Wgds8Gp/gU33kqBtgNS4tSPHuGibyoeqMV/TJlSKda6FXzoEyYGjTe+vXA==';
    script.crossOrigin = 'anonymous';
    script.referrerPolicy = 'no-referrer';
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
}
imageButton.addEventListener('click', async () => {
  const active = panels.find(panel => !panel.hidden);
  const status = document.querySelector('#export-status');
  status.hidden = true;
  imageButton.disabled = true;
  try {
    imageLibrary ||= loadImageLibrary();
    await imageLibrary;
    const canvas = await window.html2canvas(active, {backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(), scale: 1, logging: false});
    const link = document.createElement('a');
    link.href = canvas.toDataURL('image/png');
    link.download = 'trendradar-news.png';
    link.click();
  } catch {
    imageLibrary = null;
    status.textContent = '图片导出失败，可使用打印 / PDF。';
    status.hidden = false;
  } finally {
    imageButton.disabled = false;
  }
});
document.querySelector('#export').addEventListener('click', () => {
  const active = panels.find(panel => !panel.hidden);
  const name = buttons.find(button => button.getAttribute('aria-pressed') === 'true').textContent;
  const lines = ['# ' + name, document.querySelector('#collected').textContent];
  for (const section of active.querySelectorAll('section:not([hidden])')) {
    lines.push('', '## ' + section.querySelector('h3').textContent.trim());
    for (const entry of section.querySelectorAll('li:not([hidden])')) {
      const link = entry.querySelector('.news-title');
      lines.push('- ' + link.textContent + ' · ' + entry.querySelector('.source-meta').textContent + ' <' + link.href + '>');
      const reference = entry.querySelector('[data-chinese-reference]');
      if (reference) {
        const label = reference.querySelector('.reference-label').textContent;
        lines.push('  ' + label + '：' + reference.textContent.slice(label.length));
      }
    }
  }
  const url = URL.createObjectURL(new Blob([lines.join(String.fromCharCode(10))], {type: 'text/markdown;charset=UTF-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'trendradar-news.md';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
</script>""", heading=False)


def _upload(key, content, cache_control):
    import alibabacloud_oss_v2 as oss

    cfg = oss.config.load_default()
    cfg.region = "cn-hongkong"
    cfg.credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
    oss.Client(cfg).put_object(oss.PutObjectRequest(
        bucket=os.environ["MARKET_PUBLIC_NEWS_BUCKET"], key=key, body=content,
        content_type="text/html; charset=utf-8", cache_control=cache_control,
    ))


def publish_home(current, new, now, mode, *, hotlist=(), regions=None, brief=None, today=None, references=None):
    if public_origin():
        page = render_home(current, new, now, mode, hotlist=hotlist, regions=regions, brief=brief, today=today, references=references)
        _upload("index.html", page, "public, max-age=120")


def publish_edition(db, key, edition, origin, now, *, retention_days=30):
    from .admin_view import render_digest

    expected = render_digest(public_edition(edition), public=True)
    status_path = Path(os.environ["MARKET_STATUS_PATH"])
    # The collector and trading commands share this path on PVE.
    with (status_path.parent / ".news-publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _upload(f"digests/{edition['id']}.html", expected, "public, max-age=300")
        url = digest_url(origin, edition["id"], public=True)
        response = requests.get(url, params={'check': sha256(expected).hexdigest()[:16]},
                                timeout=(4, 10), allow_redirects=False)
        if response.status_code != 200 or response.content != expected:
            raise ValueError("public digest readback failed")
        with db:
            db.execute("INSERT OR IGNORE INTO published_digest_editions(delivery_key) VALUES (?)", (key,))
        rows = db.execute("""SELECT e.created_at,e.payload FROM digest_editions e
            JOIN published_digest_editions p USING (delivery_key)
            WHERE julianday(e.created_at)>=julianday(?) ORDER BY julianday(e.created_at) DESC LIMIT 200""",
            ((now - timedelta(days=retention_days)).isoformat(),))
        items = []
        for stamp, raw in rows:
            brief = json.loads(raw)
            time = datetime.fromisoformat(stamp).strftime("%m-%d %H:%M")
            count = len(brief["events"])
            items.append(f'<li><a href="{brief["id"]}.html">{escape(brief["title"])}</a>'
                         f' <small>{time} · {count} 条</small></li>')
        history = '<ul class="history">' + "".join(items) + "</ul>" if items else "<p>暂无简报。</p>"
        _upload("digests/index.html", _page("简报历史", history, history=True), "public, max-age=120")


def backfill_delivered(engine):
    origin = public_origin()
    if not origin:
        raise ValueError("Public news site is not configured")
    rows = engine.db.execute("""SELECT e.delivery_key,e.payload FROM digest_editions e
        WHERE EXISTS (SELECT 1 FROM deliveries d WHERE d.event_id=e.delivery_key AND d.state='delivered')
        AND NOT EXISTS (SELECT 1 FROM published_digest_editions p WHERE p.delivery_key=e.delivery_key)
        ORDER BY julianday(e.created_at)""").fetchall()
    for key, raw in rows:
        publish_edition(engine.db, key, json.loads(raw), origin, engine.now(),
                        retention_days=int(engine.settings.get("retention_days", 30)))
    return len(rows)
