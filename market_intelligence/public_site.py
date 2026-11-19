"""Publish only the static, public projection of TrendRadar's news and digests."""

from datetime import datetime, timedelta
import fcntl
import json
from hashlib import sha256
from html import escape
from ipaddress import ip_address
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from trendradar.report.rss_views import rss_groups

from .digests import digest_url, safe_url
from .news_topics import NewsTopics, TOPIC_LABELS


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


SITE_STYLE = """
:root { color-scheme: dark; background:#10141d; color:#eef3fb; font:1rem/1.65 system-ui,sans-serif; }
body { max-width:65rem; margin:0 auto; padding:1.2rem; }
a { color:#8de3f6; overflow-wrap:anywhere; }
nav { display:flex; flex-wrap:wrap; gap:1rem; margin-bottom:1.6rem; }
h1 { margin:0 0 .3rem; } h2 { font-size:1.12rem; margin:.7rem 0 .25rem; }
p { margin:0 0 .65rem; } ul { list-style:none; padding:0; margin:.3rem 0 1rem; }
li { padding:.54rem 0; border-bottom:1px solid #354255; }
small,.muted { color:#b0bdce; }
button,input { color:inherit; background:#18202d; font:inherit; border:1px solid #64748b; border-radius:.5rem; padding:.4rem .65rem; }
button { cursor:pointer; } button[aria-pressed=true] { border-color:#8de3f6; color:#8de3f6; }
.tools { display:flex; flex-wrap:wrap; gap:.6rem; align-items:center; margin:1rem 0; }
.tools input { flex:1; min-width:min(100%,15rem); }
:focus-visible { outline:2px solid #8de3f6; outline-offset:3px; }
[hidden] { display:none!important; }
@media print {
  :root { color-scheme:light; color:#111; background:#fff; }
  nav,.tools { display:none; }
  a,small,.muted { color:#222; }
  li { break-inside:avoid; }
}
"""


def _page(title, body, script=""):
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · TrendRadar</title><style>{SITE_STYLE}</style></head>
<body><nav aria-label="主导航"><a href="/">新闻首页</a><a href="/digests/index.html">简报历史</a></nav>
<main><h1>{title}</h1>{body}</main>{script}</body></html>""".encode()


def _rss_panel(groups, view):
    output = []
    for group in groups:
        items = []
        for article in group["titles"]:
            url = external_url(article.get("url", ""))
            title = escape(article.get("title", ""))
            if not url or not title:
                continue
            author = escape(article.get("source_name", ""))
            stamp = escape(article.get("time_display", ""))
            badge = " · 新增" if article.get("is_new") and view == "all" else ""
            items.append(f'<li><a href="{escape(url)}" rel="noreferrer">{title}</a>'
                         f'<br><small>{author} {stamp}{badge}</small></li>')
        if items:
            output.append((f'<section><h2>{escape(group["word"])}</h2><ul>{"".join(items)}</ul></section>', len(items)))
    total = sum(count for _, count in output)
    content = "".join(html for html, _ in output) or "<p>暂无新闻。</p>"
    hidden = "" if view == "recent" else " hidden"
    return f'<div data-view="{view}"{hidden}>{content}</div>', total


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
                "title": title, "url": url, "source_name": names.get(source, source),
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


def render_home(current, new, now, mode="current", *, hotlist=()):
    recent, all_groups = rss_groups(current, new, mode)
    recent_html, recent_count = _rss_panel(recent, "recent")
    all_html, all_count = _rss_panel(all_groups, "all")
    label = "今日全部" if mode == "daily" else "当前全部"
    hot_html, hot_count = _rss_panel(hotlist, "hot")
    switch = f'<button type="button" data-show="recent" aria-pressed="true">本轮新增 {recent_count}</button>'
    if mode != "incremental":
        switch += f'<button type="button" data-show="all" aria-pressed="false">{label} {all_count}</button>'
    switch += f'<button type="button" data-show="hot" aria-pressed="false">热点榜单 {hot_count}</button>'
    body = (f'<p class="muted">本轮采集 {now:%m-%d %H:%M} · 北京时间</p>'
            f'<div class="tools" aria-label="新闻视图">{switch}<input type="search" aria-label="搜索新闻标题"'
            f' placeholder="搜索新闻标题"><button type="button" id="export">导出 Markdown</button>'
            f'<button type="button" id="image">导出图片</button>'
            f'<button type="button" id="print">打印 / PDF</button></div><p id="export-status" role="status" hidden></p>'
            f'{recent_html}{all_html if mode != "incremental" else ""}{hot_html}')
    return _page("TrendRadar 新闻", body, """<script>
const panels = [...document.querySelectorAll('[data-view]')];
const buttons = [...document.querySelectorAll('[data-show]')];
const search = document.querySelector('input[type=search]');
function filterNews() {
  const query = search.value.trim().toLowerCase();
  for (const panel of panels) {
    for (const section of panel.querySelectorAll('section')) {
      const entries = [...section.querySelectorAll('li')];
      for (const entry of entries) entry.hidden = !entry.querySelector('a').textContent.toLowerCase().includes(query);
      section.hidden = entries.every(entry => entry.hidden);
    }
  }
}
for (const button of buttons) button.addEventListener('click', () => {
  for (const option of buttons) option.setAttribute('aria-pressed', String(option === button));
  for (const panel of panels) panel.hidden = panel.dataset.view !== button.dataset.show;
});
search.addEventListener('input', filterNews);
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
    const canvas = await window.html2canvas(active, {backgroundColor: '#10141d', scale: 1, logging: false});
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
  const lines = ['# ' + name, document.querySelector('main > p').textContent];
  for (const section of active.querySelectorAll('section:not([hidden])')) {
    lines.push('', '## ' + section.querySelector('h2').textContent);
    for (const entry of section.querySelectorAll('li:not([hidden])')) {
      const link = entry.querySelector('a');
      lines.push('- ' + link.textContent + ' · ' + entry.querySelector('small').textContent + ' <' + link.href + '>');
    }
  }
  const url = URL.createObjectURL(new Blob([lines.join(String.fromCharCode(10))], {type: 'text/markdown;charset=UTF-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'trendradar-news.md';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
</script>""")


def _upload(key, content, cache_control):
    import alibabacloud_oss_v2 as oss

    cfg = oss.config.load_default()
    cfg.region = "cn-hongkong"
    cfg.credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
    oss.Client(cfg).put_object(oss.PutObjectRequest(
        bucket=os.environ["MARKET_PUBLIC_NEWS_BUCKET"], key=key, body=content,
        content_type="text/html; charset=utf-8", cache_control=cache_control,
    ))


def publish_home(current, new, now, mode, *, hotlist=()):
    if public_origin():
        _upload("index.html", render_home(current, new, now, mode, hotlist=hotlist), "public, max-age=120")


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
        history = "<ul>" + "".join(items) + "</ul>" if items else "<p>暂无简报。</p>"
        _upload("digests/index.html", _page("简报历史", history), "public, max-age=120")


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
