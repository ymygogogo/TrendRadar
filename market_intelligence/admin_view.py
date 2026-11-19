"""Read-only dashboard and settings form markup for the authenticated admin."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .model_settings import FIELDS as MODEL_FIELDS


CHANNEL_NAMES = {
    "bark": "Bark",
    "feishu": "飞书",
    "telegram": "Telegram",
    "ntfy": "ntfy",
    "wework": "企业微信",
    "generic_webhook": "通用 Webhook",
}
SOURCE_TYPES = {
    "domestic_official": "国内官方",
    "domestic_media": "国内媒体",
    "international_official": "海外官方",
    "international_media": "海外媒体",
    "international_company": "海外企业原发",
    "international_social": "海外社交",
    "international_video": "海外视频",
}
DELIVERY_STATES = {"delivered": "成功", "failed": "失败", "pending": "待投递"}
VERIFICATION_STATES = {"confirmed": "已确认", "corroborated": "多源印证", "unverified": "待核实"}
SHANGHAI = ZoneInfo("Asia/Shanghai")

STYLE = """
:root { color-scheme: dark; --bg:#10141d; --panel:#18202d; --edge:#354255;
        --text:#eef3fb; --muted:#b0bdce; --accent:#8de3f6; --good:#88e2bb; --warn:#ffcc88; }
* { box-sizing:border-box; }
html { scroll-padding-block-start:1rem; }
body { margin:0; background:var(--bg); color:var(--text); font:1rem/1.55 system-ui,sans-serif; }
a { color:var(--accent); text-underline-offset:.22em; }
:focus-visible { outline:2px solid var(--accent); outline-offset:4px; }
.model-usage { margin-top:1rem; }
.usage-chart { display:block; width:100%; min-height:8rem; }
.usage-chart text { fill:#b7c5da; font:13px system-ui,sans-serif; }
.usage-key { display:flex; gap:1rem; flex-wrap:wrap; }
.usage-key span:first-child { color:#55d9f0; }
.usage-key span:nth-child(2) { color:#b9a5ff; }
.attempt-key span:first-child { color:#74e6a6; }
.model-usage summary { cursor:pointer; width:fit-content; }
.top { background:var(--panel); border-block-end:1px solid var(--edge); }
.bar, main { width:min(100% - 2rem, 75rem); margin-inline:auto; }
.bar { display:flex; align-items:center; justify-content:space-between; gap:.5rem 1.5rem;
       flex-wrap:wrap; padding-block:.75rem; }
.brand { font-weight:760; letter-spacing:.025em; }
nav { display:flex; flex-wrap:wrap; gap:.3rem 1rem; }
nav a { display:inline-block; padding:.3rem 0; }
main { padding-block:1.75rem 3rem; }
section { margin-block-end:2rem; }
h1,h2,h3,p,dl { margin:0; }
h1 { font-size:clamp(1.65rem,4vw,2.2rem); letter-spacing:.02em; }
h2 { font-size:1.35rem; }
h3 { font-size:1rem; }
.heading { display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap;
           gap:.25rem .75rem; margin-block-end:.75rem; }
.heading p,.muted { color:var(--muted); font-size:.9rem; }
.panel,.stats > div { background:var(--panel); border:1px solid var(--edge); border-radius:.8rem; }
.panel { padding:clamp(.85rem,2vw,1.3rem); min-width:0; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; margin-block-end:.75rem; }
.stats > div { padding:.8rem 1rem; }
.queue-note { margin-block-end:.75rem; }
dt { color:var(--muted); font-size:.9rem; }
dd { margin:0; font-size:1.8rem; font-weight:700; color:var(--text); font-variant-numeric:tabular-nums; }
dd small { font-size:.85rem; font-weight:400; color:var(--muted); }
.events { list-style:none; padding:0; margin:.25rem 0 0; }
.events li { display:grid; grid-template-columns:2.65rem minmax(0,1fr); align-items:start;
             gap:.75rem; padding:.75rem 0; border-block-end:1px solid var(--edge); }
.events li:last-child { border:0; padding-block-end:0; }
.event-title { font-size:1rem; font-weight:650; overflow-wrap:anywhere; }
.event-summary { overflow-wrap:anywhere; margin:.1rem 0; }
.tag { display:inline-block; width:max-content; border:1px solid var(--edge); border-radius:.35rem;
       padding:.05rem .38rem; font-size:.8rem; font-weight:650; white-space:nowrap; }
.good { color:var(--good); } .warn { color:var(--warn); } .info { color:var(--accent); }
.channels { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); gap:.75rem; }
.scroll { overflow-x:auto; margin-block-start:.5rem; }
table { width:100%; border-collapse:collapse; text-align:left; font-size:.9rem; }
th,td { padding:.55rem .55rem .55rem 0; border-block-end:1px solid var(--edge); vertical-align:top; }
thead th { color:var(--muted); font-weight:600; white-space:nowrap; }
tbody th { font-weight:650; overflow-wrap:anywhere; min-width:6rem; }
tbody tr:last-child > * { border-block-end:0; }
.cell-detail { display:block; color:var(--muted); font-size:.8rem; white-space:nowrap; }
.time-cell { white-space:nowrap; }
form label { display:block; color:var(--text); font-weight:550; }
.form-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.75rem 1rem; margin-block-end:1rem; }
input,textarea { background:var(--bg); border:1px solid #687b92; border-radius:.4rem;
                 width:100%; display:block; margin:.3rem 0 0; padding:.45rem .6rem; color:inherit; font:inherit; }
textarea { resize:vertical; }
button { background:var(--accent); color:#0e2530; border:0; border-radius:.4rem;
         font:inherit; font-weight:700; padding:.55rem 1rem; margin-block-start:.85rem; cursor:pointer; }
.message { margin-block-end:.65rem; color:var(--accent); }
@media (max-width:62rem) { .channels { grid-template-columns:minmax(0,1fr); } }
@media (max-width:39rem) {
  .stats,.form-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .stats > div { padding:.65rem; }
  main { padding-block-start:1.15rem; }
  tbody th { min-width:4.25rem; }
  th,td { padding-inline-end:.32rem; }
  .cell-detail { white-space:normal; }
}
@media (max-width:23rem) { .form-grid { grid-template-columns:minmax(0,1fr); } }
"""


def local_time(value, full=False):
    if not value:
        return "—"
    try:
        time = datetime.fromisoformat(value).astimezone(SHANGHAI)
    except ValueError:
        return "—"
    label = time.strftime("%Y-%m-%d %H:%M" if full else "%m-%d %H:%M")
    return f'<time datetime="{time.isoformat()}">{label}</time>'


def render_overview(snapshot):
    if not snapshot:
        return '<p class="panel muted">等待首轮采集生成状态快照。</p>'
    articles = snapshot["articles"]
    events = snapshot.get("events", {})
    deliveries = snapshot.get("deliveries", {})
    cards = "".join(
        f'<div><dt>{label}</dt><dd aria-label="{label} {count} {unit}">{count:,} <small>{unit}</small></dd></div>'
        for label, count, unit in (
            ("待分析", articles.get("pending", 0), "篇"),
            ("已分析", articles.get("done", 0), "篇"),
            ("P0 / P1 事件", events.get("P0", 0) + events.get("P1", 0), "件"),
            ("成功投递", deliveries.get("delivered", 0), "条"),
        )
    )
    rows = "".join(
        f'<li><span class="tag {"warn" if priority == "P0" else "info"}">{escape(priority)}</span>'
        f'<div><p class="event-title">{escape(title)}</p><p class="event-summary">{escape(summary)}</p>'
        f'<p class="muted">{score} 分 · {VERIFICATION_STATES.get(verified, "核实状态未知")} · '
        f'最近报道 {local_time(stamp)}</p></div></li>'
        for title, summary, priority, score, verified, stamp in snapshot.get("recent_events", [])
    )
    recent = f'<ul class="events">{rows}</ul>' if rows else '<p class="muted">过去 24 小时暂无 P0 / P1 事件。</p>'
    excluded = articles.get("filtered", 0)
    expired = articles.get("expired", 0)
    archived = (f'<p class="muted queue-note">仅存档 {excluded:,} 篇 · 已过期 {expired:,} 篇</p>'
                if excluded or expired else "")
    return f'<dl class="stats">{cards}</dl>{archived}<div class="panel"><h3>近 24 小时重点事件</h3>{recent}</div>'


def render_sources(snapshot):
    details = {row[0]: row[1:] for row in snapshot.get("source_details", [])}
    rows = []
    for feed, attempts, successes, failures, duration in snapshot.get("sources", []):
        name, category, last_attempt, last_success, latest = details.get(feed, (feed, "", None, None, None))
        source_type = SOURCE_TYPES.get(category, category)
        state = f"连续失败 {failures} 次" if failures else "正常" if attempts else "等待采集"
        elapsed = f"{duration:,} ms" if duration is not None else "—"
        sample = f"成功 {successes}/{attempts} 次" if attempts else "无采集记录"
        rows.append(f'<tr><th scope="row">{escape(name)}<span class="cell-detail">{escape(source_type)}</span></th>'
                    f'<td><span class="tag {"warn" if failures else "good" if attempts else ""}">{state}</span>'
                    f'<span class="cell-detail">{sample} · {elapsed}</span>'
                    f'<span class="cell-detail">最近抓取 {local_time(last_attempt)}</span></td>'
                    f'<td><span class="cell-detail">成功 {local_time(last_success)}</span>'
                    f'<span class="cell-detail">发稿 {local_time(latest)}</span></td></tr>')
    if not rows:
        return '<p class="muted">暂无采集渠道记录。</p>'
    return ('<div class="scroll" role="region" aria-labelledby="source-title" tabindex="0"><table>'
            '<thead><tr><th scope="col">来源</th><th scope="col">抓取状态</th><th scope="col">最近时间</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def render_notifications(snapshot):
    rows = []
    for channel, data in sorted(snapshot.get("notification_channels", {}).items(),
                                key=lambda item: (not item[1]["enabled"], item[0])):
        counts, latest = data["counts"], data["latest"]
        if data["enabled"]:
            label, tone = ("已启用", "good") if data["configured"] else ("待配置", "warn")
        else:
            label, tone = "未启用", ""
        last = (f'{DELIVERY_STATES.get(latest[0], "未知")} <span class="cell-detail">{local_time(latest[1])}</span>'
                if latest else "暂无记录")
        sent = counts.get("delivered", 0)
        failed = counts.get("failed", 0)
        rows.append(f'<tr><th scope="row">{escape(CHANNEL_NAMES.get(channel, channel))}</th>'
                    f'<td><span class="tag {tone}">{label}</span></td>'
                    f'<td class="time-cell">{last}</td>'
                    f'<td class="time-cell">{sent} 成功<span class="cell-detail">{failed} 失败</span></td></tr>')
    if not rows:
        return '<p class="muted">等待下一次运行同步推送配置。</p>'
    return ('<div class="scroll" role="region" aria-labelledby="delivery-title" tabindex="0"><table>'
            '<thead><tr><th scope="col">渠道</th><th scope="col">配置状态</th><th scope="col">最近投递</th>'
            f'<th scope="col">投递记录</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _usage_chart(rows, series, title):
    values = [row[key] for row in rows for key, _ in series if row.get(key) is not None]
    if not values:
        return '<p class="muted">等待模型返回 Token 用量。</p>'
    peak = max(1, max(values))
    lines = []
    for key, color in series:
        segment = []
        for index, row in enumerate(rows):
            x = 52 + index * (540 / max(1, len(rows) - 1))
            value = row.get(key)
            if value is None:
                if segment:
                    lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{" ".join(segment)}"/>')
                    segment = []
                continue
            y = 132 - value / peak * 100
            segment.append(f"{x:.1f},{y:.1f}")
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>')
        if segment:
            lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{" ".join(segment)}"/>')
    dates = "".join(
        f'<text x="{52 + i * (540 / max(1, len(rows)-1)):.1f}" y="155" text-anchor="middle">{escape(row["date"][5:])}</text>'
        for i, row in enumerate(rows))
    return (f'<svg viewBox="0 0 620 172" role="img" aria-label="{escape(title)}" class="usage-chart">'
            '<path d="M52 32H592M52 82H592M52 132H592" stroke="#354255"/>'
            f'<text x="50" y="24">{peak:,}</text><text x="30" y="135">0</text>{"".join(lines)}{dates}</svg>')


def render_model(model, usage, token, message):
    note = f'<p class="message" role="status">{escape(message)}</p>' if message else ""
    if model:
        numbers = "".join(
            f'<label>{label}<input name="{key}" type="number" min="{minimum}" max="{maximum}" '
            f'required value="{int(model["selected_" + key])}"></label>'
            for key, label, minimum, maximum in MODEL_FIELDS
        )
        state = "已配置" if model["credential_configured"] else "缺少凭据"
        enabled = "已启用" if model["analysis_enabled"] else "已关闭"
        host = escape(model["endpoint_host"]) or "服务商默认地址"
        configuration = f"""<form action="/admin/model#model" method="post" class="panel">
<p class="muted">最近运行使用 <strong>{escape(model['model'])}</strong> · {host} · 凭据{state} · 分析{enabled}</p>
{note}<input name="token" type="hidden" value="{escape(token)}">
<div class="form-grid"><label>模型标识<input type="text" name="model" maxlength="128"
 required value="{escape(model['selected_model'])}"></label>{numbers}</div>
<p class="muted">同一服务商下的模型变更从下轮采集生效。服务地址和凭据由部署环境管理。</p>
<button>保存模型配置</button></form>"""
    else:
        configuration = f'<div class="panel">{note}<p class="muted">等待采集容器同步模型配置。</p></div>'
    if not usage:
        return configuration
    token_chart = _usage_chart(usage, (("input_tokens", "#55d9f0"), ("output_tokens", "#b9a5ff")), "最近 7 天输入和输出 Token")
    attempts = _usage_chart(usage, (("attempts", "#74e6a6"),), "最近 7 天分析尝试次数")
    def count(value):
        return f"{value:,}" if value is not None else "—"
    table = "".join(
        f'<tr><th scope="row">{escape(row["date"])}</th><td>{count(row["attempts"])}</td>'
        f'<td>{count(row["input_tokens"])}</td><td>{count(row["output_tokens"])}</td>'
        f'<td>{row["input_samples"]}/{row["responses"]} · {row["output_samples"]}/{row["responses"]}</td></tr>'
        for row in usage)
    return configuration + f"""<div class="panel model-usage">
<h3>模型用量 · 最近 7 天</h3>
<div class="channels"><div><p class="usage-key"><span>输入 Token</span><span>输出 Token</span></p>{token_chart}</div>
<div><p class="usage-key attempt-key"><span>分析尝试次数</span></p>{attempts}</div></div>
<p class="muted">Token 来自模型响应。空白表示未采集到用量。分析尝试包含失败重试。</p>
<details><summary>查看每日用量</summary><div class="scroll"><table>
<thead><tr><th>北京时间</th><th>尝试</th><th>输入 Token</th><th>输出 Token</th><th>有用量的响应（输入 · 输出）</th></tr></thead>
<tbody>{table}</tbody></table></div></details></div>"""


def render_dashboard(settings, fields, token, message, snapshot, *, news_url="", model=None, model_message=""):
    data = snapshot or {}
    numbers = "".join(
        f'<label>{label}<input name="{key}" type="number" required value="{int(settings[key])}"></label>'
        for key, label in fields
    )
    terms = escape("\n".join(settings["ignore_title_terms"]))
    note = f'<p class="message" role="status">{escape(message)}</p>' if message else ""
    updated = f'更新于 {local_time(data["updated_at"], full=True)} · 北京时间' if data else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrendRadar 管理</title><style>{STYLE}</style>
</head>
<body>
<header class="top"><div class="bar">
<span class="brand">TrendRadar 管理</span>
<nav aria-label="页面导航">
<a href="#overview">总览</a><a href="#channels">渠道</a><a href="#model">模型与用量</a><a href="#settings">设置</a>
<a href="{escape(news_url or "/news/")}">新闻报告</a>
</nav></div></header>
<main>
<section id="overview" aria-labelledby="overview-title">
<div class="heading"><h1 id="overview-title">当前汇总</h1><p>{updated}</p></div>
{render_overview(snapshot)}
</section>
<section id="channels" aria-labelledby="channels-title">
<div class="heading"><h2 id="channels-title">渠道</h2><a href="/admin/metrics">采集指标</a></div>
<div class="channels">
<div class="panel"><h3 id="source-title">RSS 采集渠道</h3>{render_sources(data)}</div>
<div class="panel"><h3 id="delivery-title">推送渠道</h3>{render_notifications(data)}</div>
</div></section>
<section id="model" aria-labelledby="model-title">
<div class="heading"><h2 id="model-title">模型与用量</h2></div>
{render_model(model or {}, data.get("model_usage", []), token, model_message)}
</section>
<section id="settings" aria-labelledby="settings-title">
<div class="heading"><h2 id="settings-title">运行设置</h2><p>保存后从下一轮任务生效。</p></div>
{note}
<form class="panel" action="/admin/" method="post"><input name="token" type="hidden" value="{escape(token)}">
<div class="form-grid">{numbers}</div>
<label>媒体标题过滤词，每行一个<textarea name="ignore_title_terms" rows="6">{terms}</textarea></label>
<button>保存设置</button></form>
</section>
</main>
</body></html>""".encode()


def _evidence_link(url, label):
    from .digests import safe_url

    return f'<a href="{escape(url)}" rel="noreferrer">{label}</a>' if safe_url(url) else ""


def _digest_event(event, number):
    from .digests import DIRECTIONS_ZH, REGION_LABELS
    from .news_topics import TOPIC_LABELS

    tier = {"watch": " · 观察", "extended": " · 补录"}.get(event["tier"], "")
    stock_rows = "".join(
        f'<li><strong>{escape(stock["name"])} {escape(stock["code"])}</strong> · '
        f'{escape("、".join(stock["sectors"]))} · {escape(stock["business"])} '
        f'{_evidence_link(stock["source"], "公司业务证据")}</li>'
        for stock in event["companies"]
    )
    stocks = f'<ul>{stock_rows}</ul>' if stock_rows else "<p>暂无已核实的相关股票</p>"
    sector_rows = "".join(
        f'<li><strong>{escape(sector["sector"])}</strong> · '
        f'{DIRECTIONS_ZH.get(sector["direction"], "影响不明确")} · {escape(sector["reason"])}</li>'
        for sector in event["sectors"]
    )
    sectors = f'<ul>{sector_rows}</ul>' if sector_rows else "<p>暂无明确关联板块</p>"
    source = _evidence_link(event["source"], "新闻原文")
    sources = " ".join(_evidence_link(url, "同事件报道") for url in event.get("other_sources", []))
    evidence = event.get("sources", [])
    if evidence:
        entries = []
        for item in evidence:
            region = "（海外）" if item["international"] else "（国内）" if item.get("domestic") else ""
            name = item["name"]
            medium = item.get("medium")
            if medium and medium not in name:
                name += f" · {medium}"
            entry = _evidence_link(item["url"], escape(name + region))
            if entry and (stats := item.get("youtube_views")):
                stamp = local_time(stats["sampled_at"], full=True)
                entry += f' <small>YouTube 播放 {stats["views"]:,} 次（{stamp} 采集）</small>'
            entries.append(entry)
        source = " · ".join(filter(None, entries))
        shown_urls = {item["url"] for item in evidence}
        sources = " ".join(_evidence_link(url, "同事件报道") for url in event.get("other_sources", [])
                           if url not in shown_urls)
    concepts = escape("、".join(event.get("concepts", []))) or "暂无明确关联概念"
    topic = TOPIC_LABELS.get(event.get("topic"))
    heat = ""
    if topic:
        region = REGION_LABELS.get(event.get("edition_region"))
        prefix = f"{region} · {topic}" if region else topic
        heat = f"{prefix} · 热度 {event['heat']} · {event['outlet_count']} 家来源"
        if event["hotlist_rank"] is not None:
            heat += f" · 近期榜单最高第 {event['hotlist_rank']} 名"
        heat = f'<p class="muted">{heat}</p>'
    return f"""<article class="panel brief-event" id="event-{number}">
<h2>{number}. {escape(event['title'])}</h2>
{heat}<p class="muted">{escape(event['priority'])}{tier} · A 股影响评分 {event['score']} 分 ·
{VERIFICATION_STATES.get(event['verification'], '待核实')} · 最近报道 {local_time(event['reported_at'], full=True)}</p>
<h3>事件事实</h3><p>{escape(event['summary'])}</p><p>{source} {sources}</p>
<h3>A 股潜在影响</h3><p>{DIRECTIONS_ZH.get(event['direction'], '影响不明确')} · {escape(event['impact'] or '暂无足够证据')}</p>
<h3>相关概念与板块</h3><p>{concepts}</p>{sectors}
<h3>相关股票与证据</h3>{stocks}
</article>"""


def render_digest(edition, *, public=False):
    from .digests import region_summary

    navigation = ('<a href="/">RSS 新闻</a> · <a href="/digests/index.html">简报历史</a>'
                  if public else '<a href="/admin/">返回 TrendRadar 管理</a>')
    entries = "".join(_digest_event(event, number) for number, event in enumerate(edition["events"], 1))
    actual = len(edition["events"])
    missing = max(0, edition["target"] - actual)
    status = f"收录 {actual} 条"
    if regions := region_summary(edition["events"]):
        status += f" · {regions}"
    if missing:
        status += f" · 距目标还差 {missing} 条"
    context = ""
    if edition.get("market_context"):
        rows = "".join(
            f'<li>{escape(item["state"])} · {local_time(item["time"])}{escape(item["time_label"])} · '
            f'{escape(item["title"])} {_evidence_link(item["url"], "原文")}'
            f'<p class="muted">{escape(item["impact"])}</p></li>' for item in edition["market_context"]
        )
        context = ('<section id="market-context" class="panel brief-event"><h2>市场核实与传闻跟踪</h2>'
                   f'<ul>{rows}</ul></section>')
    closing = ""
    if edition.get("closing"):
        from .market_close import SOURCE

        closing_rows = "".join(
            f'<li>{escape(line.removeprefix("- ").split("[上交所]", 1)[0])}</li>'
            for line in edition["closing"] if not line.startswith("同日市场对照")
        )
        closing = ('<section class="panel brief-event"><h2>当日收盘指数</h2>'
                   f'<ul>{closing_rows}</ul>{_evidence_link(SOURCE, "上交所指数来源")}</section>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(edition['title'])} · TrendRadar</title><style>{STYLE}
.brief {{ max-width:61rem; }}
.brief-event {{ margin-block:1rem; overflow-wrap:anywhere; scroll-margin-block-start:1rem; }}
.brief-event:target {{ outline:2px solid var(--accent); }}
.brief-event h3 {{ margin-block:.85rem .15rem; color:var(--accent); }}
.brief-event ul {{ margin-block:0; padding-inline-start:1.3rem; }}
.brief-event li {{ margin-block:.25rem; }}
</style></head><body>
<header class="top"><div class="bar">{navigation}</div></header>
<main class="brief"><h1>{escape(edition['title'])}</h1>
<p class="muted">{local_time(edition['created_at'], full=True)} · 北京时间 · {status}</p>
{context}{entries}{closing}</main></body></html>""".encode()
