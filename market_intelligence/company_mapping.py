"""Match specific affected industries against verified issuer business records."""
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml


def load_companies(path):
    try:
        with Path(path).open(encoding="utf-8") as source:
            companies = yaml.safe_load(source)["companies"]
    except yaml.YAMLError as error:
        raise ValueError("Invalid issuer evidence file") from error
    if not isinstance(companies, list):
        raise ValueError("Expected a company evidence list")
    seen = set()
    for item in companies:
        code = item["code"]
        url = urlsplit(item["source"])
        if (not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) or code in seen
                or url.scheme != "https" or not url.hostname or url.username or url.password
                or not item["name"].strip() or not item["business"].strip()
                or not isinstance(item["sectors"], list)
                or not all(isinstance(sector, str) and sector.strip() for sector in item["sectors"])):
            raise ValueError("Invalid evidence record for an A-share issuer")
        seen.add(code)
    return companies


def match_companies(companies, sectors, limit=4):
    affected = {item["sector"].strip() for item in sectors}
    results = []
    for item in companies:
        matched = sorted(affected.intersection(item["sectors"]))
        if matched:
            results.append({"code": item["code"], "name": item["name"],
                            "sectors": matched, "business": item["business"], "source": item["source"]})
    return results[:limit]
