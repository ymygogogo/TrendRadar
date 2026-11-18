"""Collect published Shanghai Stock Exchange index closing snapshots."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SOURCE = "https://www.sse.com.cn/market/sseindex/overview/"
QUERY = "https://query.sse.com.cn/commonQuery.do?" + urlencode({
    "jsonCallBack": "trendradar",
    "isPagination": "false",
    "sqlId": "COMMON_SSE_SJ_SZZS_ZDZSJYBBXXX",
})


def parse_snapshot(raw, codes):
    match = re.fullmatch(r"trendradar\((.*?)\);?", raw.strip(), flags=re.S)
    if not match:
        raise ValueError("Unexpected official market data wrapper")
    snapshots = {}
    for record in json.loads(match[1])["result"]:
        code = record["INDEX_CODE"]
        if code not in codes:
            continue
        day = datetime.strptime(record["TRADE_DATE"], "%Y%m%d").date()
        point = Decimal(record["CLOSE_POINT"])
        amount = Decimal(record["TRADE_AMT"])
        title = record["INDEX_ABBR"].strip()
        if not title or not point.is_finite() or not amount.is_finite() or point <= 0 or amount < 0:
            raise ValueError("Invalid public close or traded amount")
        if (day, code) in snapshots:
            raise ValueError("Duplicate exchange snapshot")
        snapshots[day, code] = (title, point, amount)
    if not snapshots:
        raise ValueError("No configured benchmark in the official response")
    return snapshots


def fetch_snapshot(codes):
    request = Request(QUERY, headers={"Referer": SOURCE, "Accept": "application/javascript"})
    with urlopen(request, timeout=10) as response:
        payload = response.read(131073)
    if len(payload) > 131072:
        raise ValueError("Public market response exceeds the expected size")
    try:
        return parse_snapshot(payload.decode("utf-8"), codes)
    except (KeyError, TypeError, InvalidOperation) as error:
        raise ValueError("Invalid official close response") from error
