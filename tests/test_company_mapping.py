from pathlib import Path

from market_intelligence.company_mapping import load_companies, match_companies


COVERED = Path(__file__).resolve().parents[1] / "config/market_companies.yaml"


def test_issuer_evidence_maps_only_precise_affected_industry():
    companies = load_companies(COVERED)
    match = match_companies(companies, [{"sector": "刻蚀设备"}, {"sector": "泛科技"}])
    assert {company["code"] for company in match} == {"002371.SZ", "688012.SH"}
    assert all(company["source"].startswith("https://") and company["business"] for company in match)
    assert not match_companies(companies, [{"sector": "新能源汽车概念"}])
