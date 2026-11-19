"""A-share opening observations and publicly reported context."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

import requests

from .digests import safe_url


SOURCE = "https://finance.sina.com.cn/realstock/company/sh000001/nc.shtml"
CODES = {"sh000001": "沪指", "sz399001": "深成指", "sz399006": "创业板指"}
QUOTE = "https://hq.sinajs.cn/list=" + ",".join(CODES)
SIGNAL = re.compile(r"断供|停单|砍单|穿透.{0,8}(审查|严查)|辟谣|不实|谣言|不属实|官方回应|据传|网传|市场传闻|小作文|出口管制|出口限制")
MARKET = re.compile(r"芯片|GPU|算力|代工|半导体|流片|基金|证监会|股市|救市|资本市场|交易所|敞口|证券", re.I)
CORRECTION = re.compile(r"辟谣|不实|谣言|不属实|传闻不准确|不准确")
OFFICIAL = frozenset({"csrc-news", "market-rumor-corrections"})
SUPPLY = re.compile(r"断供|停单|砍单|穿透|代工|流片|出口管制|出口限制")


def parse_open(raw, day):
    values = {}
    for code, line in re.findall(r'var hq_str_(\w+)\s*=\s*"([^"]*)"', raw):
        if code not in CODES:
            continue
        fields = line.split(",")
        dates = [field for field in fields[20:] if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", field)]
        if dates != [day.isoformat()]:
            continue
        try:
            opening, previous = map(Decimal, fields[1:3])
            if opening.is_finite() and previous.is_finite() and min(opening, previous) > 0:
                values[code] = (opening / previous - 1) * 100
        except (ValueError, InvalidOperation):
            continue
    if set(values) != set(CODES):
        raise ValueError("Missing verified opening quotes for requested date")
    return {label: values[code] for code, label in CODES.items()}


def fetch_open(day):
    reply = requests.get(QUOTE, headers={"Referer": SOURCE}, timeout=(4, 8), allow_redirects=False)
    reply.raise_for_status()
    return parse_open(reply.content.decode("gb18030"), day)


def opening_facts(values):
    return "开盘实况 · " + " / ".join(
        f"{name}{'平开' if round(value, 2) == 0 else ('高开' if value > 0 else '低开') + f' {abs(value):.2f}%'}"
        for name, value in values.items()
    )


def market_context(db, asof, limit=3):
    cutoff = (asof - timedelta(hours=30)).isoformat()
    results, seen = [], set()
    rows = db.execute("""SELECT feed_id,title,url,published_at,collected_at FROM articles
        WHERE julianday(coalesce(published_at,collected_at)) BETWEEN julianday(?) AND julianday(?)
          AND julianday(collected_at) <= julianday(?)
        ORDER BY julianday(coalesce(published_at,collected_at)) DESC,id""", (cutoff, asof.isoformat(), asof.isoformat()))
    for source, title, url, published, collected in rows:
        url = safe_url(url)
        if not url or not SIGNAL.search(title) or not MARKET.search(title):
            continue
        key = re.sub(r"\W+", "", title).casefold()
        if key in seen:
            continue
        seen.add(key)
        official_review = source in OFFICIAL and (CORRECTION.search(title) or "官方回应" in title)
        if official_review:
            state = "官方核实"
        elif CORRECTION.search(title):
            state = "媒体核查，结论待跟踪"
        else:
            state = "公开报道，细节待核实"
        inference = ("若审查或供给约束实质收紧，海外先进制程流片及交付可能承压"
                     if SUPPLY.search(title) else "可能影响风险偏好，政策或资金变化仍需核实")
        if official_review:
            inference = "该回应可降低传闻不确定性，盘面影响仍需观察"
        when = datetime.fromisoformat(published or collected).astimezone(asof.tzinfo)
        results.append({"title": title, "url": url, "state": state, "impact": inference,
                        "time": when.isoformat(), "first_seen": collected,
                        "time_label": "报道" if published else "系统发现"})
    # Reserve the most recent supply-chain signal and the strongest correction when both exist.
    prioritized = sorted(results, key=lambda item: item["state"] != "官方核实")
    supply = next((item for item in results if SUPPLY.search(item["title"])), None)
    if supply is not None and supply not in prioritized[:limit]:
        prioritized = prioritized[:max(0, limit - 1)] + [supply]
    return prioritized[:limit]
