from pathlib import Path

import pytest

from market_intelligence import operator_settings


def test_operator_settings_atomically_persist_and_apply(tmp_path):
    path = tmp_path / "admin" / "settings.json"
    config = {"event_analysis": {"min_priority_score": 70, "urgent_priority_score": 85}}
    operator_settings.write(path, {"max_articles_per_run": 20, "min_priority_score": 75,
                                   "max_digest_events": 8, "ignore_title_terms": [" 圈粉", "圈粉"]}, config)
    operator_settings.apply(config, operator_settings.read(path, config))
    assert config["max_articles_per_run"] == 20
    assert config["event_analysis"] == {"min_priority_score": 75, "urgent_priority_score": 85}
    assert config["digest"]["max_events"] == 8
    assert config["crawl_filter"]["ignore_title_terms"] == ["圈粉"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(list(path.parent.iterdir())) == 1


@pytest.mark.parametrize("values", [
    {"AI_API_KEY": "forbidden"},
    {"max_digest_events": 7},
    {"min_priority_score": 90},
    {"ignore_title_terms": [""]},
    {"max_articles_per_run": True},
])
def test_operator_settings_reject_invalid_updates_without_replacing_file(tmp_path, values):
    path = tmp_path / "settings.json"
    operator_settings.write(path, {"max_digest_events": 8}, {})
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        operator_settings.write(path, values, {})
    assert path.read_bytes() == previous


def test_main_loader_uses_operator_options_on_next_run(monkeypatch, tmp_path):
    from trendradar.core import load_config

    source = Path(__file__).resolve().parents[1] / "config/config.yaml"
    path = tmp_path / "settings.json"
    operator_settings.write(path, {"min_priority_score": 78, "max_articles_per_run": 33}, {})
    monkeypatch.setenv("MARKET_ENABLED", "true")
    monkeypatch.setenv("MARKET_OPERATOR_SETTINGS", str(path))
    config = load_config(str(source))["MARKET_INTELLIGENCE"]
    assert config["event_analysis"]["min_priority_score"] == 78
    assert config["max_articles_per_run"] == 33


def test_existing_digest_limit_below_eight_keeps_other_overrides(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"max_digest_events": 6, "max_articles_per_run": 20}')
    assert operator_settings.read(path, {}) == {"max_digest_events": 8, "max_articles_per_run": 20}
