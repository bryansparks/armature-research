# tests/tools/test_communities.py
"""Tests for research/tools/communities.py — Hacker News, Polymarket, GitHub."""
import pytest
from unittest.mock import patch


# ── search_hackernews ─────────────────────────────────────────────────────────

async def test_hn_empty_query_returns_error():
    from research.tools.communities import _handle_search_hackernews
    result = await _handle_search_hackernews({"query": "   "})
    assert result["results"] == []
    assert "error" in result


async def test_hn_http_failure_degrades_gracefully():
    from research.tools.communities import _handle_search_hackernews
    with patch("research.tools.communities._http_get_json", side_effect=RuntimeError("boom")):
        result = await _handle_search_hackernews({"query": "rust"})
    assert result["results"] == []
    assert "boom" in result["error"]


async def test_hn_returns_structured_results():
    payload = {"hits": [
        {"objectID": "1", "title": "Rust is great", "points": 200, "num_comments": 50,
         "author": "alice", "created_at": "2026-06-01T00:00:00Z", "url": "https://example.com/1"},
        {"objectID": "2", "title": "Self post", "points": 0, "num_comments": 3,
         "author": "bob", "created_at": "2026-06-02T00:00:00Z", "url": None},
    ]}
    from research.tools.communities import _handle_search_hackernews
    with patch("research.tools.communities._http_get_json", return_value=payload):
        result = await _handle_search_hackernews({"query": "rust", "max_results": 5})
    assert result["query"] == "rust"
    assert len(result["results"]) == 2
    r0 = result["results"][0]
    assert r0["url"] == "https://example.com/1"
    assert r0["points"] == 200
    assert r0["source_type"] == "hackernews"
    assert 0.0 < r0["engagement_score"] <= 1.0
    assert "200" in r0["engagement_label"]
    # Null url falls back to the HN item URL
    assert result["results"][1]["url"] == "https://news.ycombinator.com/item?id=2"


async def test_hn_recency_filter_added_to_request():
    from research.tools.communities import _handle_search_hackernews
    captured = {}
    def fake_get(url, headers=None, timeout=15):
        captured["url"] = url
        return {"hits": []}
    with patch("research.tools.communities._http_get_json", side_effect=fake_get):
        await _handle_search_hackernews({"query": "ai", "recency_iso": "2026-05-29T00:00:00Z"})
    assert "numericFilters=created_at_i" in captured["url"]


# ── search_polymarket ──────────────────────────────────────────────────────────

async def test_polymarket_empty_query_returns_error():
    from research.tools.communities import _handle_search_polymarket
    result = await _handle_search_polymarket({"query": ""})
    assert result["results"] == []
    assert "error" in result


async def test_polymarket_http_failure_degrades_gracefully():
    from research.tools.communities import _handle_search_polymarket
    with patch("research.tools.communities._http_get_json", side_effect=RuntimeError("network")):
        result = await _handle_search_polymarket({"query": "election"})
    assert result["results"] == []
    assert "network" in result["error"]


async def test_polymarket_filters_by_query_and_returns_results():
    payload = [
        {"slug": "will-x-happen", "question": "Will the election happen in 2026?",
         "volumeNum": 2_500_000, "liquidityNum": 100000,
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.68", "0.32"]',
         "endDate": "2026-12-31T00:00:00Z"},
        {"slug": "unrelated", "question": "Will it snow?", "volumeNum": 10,
         "outcomes": "[]", "outcomePrices": "[]", "endDate": ""},
    ]
    from research.tools.communities import _handle_search_polymarket
    with patch("research.tools.communities._http_get_json", return_value=payload):
        result = await _handle_search_polymarket({"query": "election", "max_results": 5})
    assert len(result["results"]) == 1
    m = result["results"][0]
    assert m["question"].startswith("Will the election")
    assert m["volume"] == 2_500_000
    assert m["outcomes"] == ["Yes", "No"]
    assert m["odds"] == [0.68, 0.32]
    assert m["source_type"] == "polymarket"
    assert 0.0 < m["engagement_score"] <= 1.0
    assert "68%" in m["engagement_label"]


async def test_polymarket_recency_filter_excludes_old_markets():
    payload = [
        {"slug": "a", "question": "election recent", "volumeNum": 1_000_000,
         "outcomes": "[]", "outcomePrices": "[]", "endDate": "2026-06-15T00:00:00Z"},
        {"slug": "b", "question": "election old", "volumeNum": 1_000_000,
         "outcomes": "[]", "outcomePrices": "[]", "endDate": "2026-01-01T00:00:00Z"},
    ]
    from research.tools.communities import _handle_search_polymarket
    with patch("research.tools.communities._http_get_json", return_value=payload):
        result = await _handle_search_polymarket(
            {"query": "election", "recency_iso": "2026-06-01T00:00:00Z"})
    assert len(result["results"]) == 1
    end_dates = [r["endDate"] for r in result["results"]]
    assert "2026-01-01" not in end_dates


# ── search_github ──────────────────────────────────────────────────────────────

async def test_github_empty_query_returns_error():
    from research.tools.communities import _handle_search_github
    result = await _handle_search_github({"query": ""})
    assert result["results"] == []
    assert "error" in result


async def test_github_http_error_degrades_gracefully():
    import urllib.error
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json",
               side_effect=urllib.error.HTTPError("u", 403, "Forbidden", {}, None)):
        result = await _handle_search_github({"query": "rust"})
    assert result["results"] == []
    assert "403" in result["error"]


async def test_github_returns_structured_results():
    payload = {"items": [
        {"full_name": "rust-lang/rust", "html_url": "https://github.com/rust-lang/rust",
         "description": "A language", "stargazers_count": 90000, "language": "Rust",
         "updated_at": "2026-06-20T00:00:00Z"},
        {"full_name": "other/repo", "html_url": "https://github.com/other/repo",
         "description": None, "stargazers_count": 0, "language": None,
         "updated_at": "2026-06-21T00:00:00Z"},
    ]}
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json", return_value=payload) as mock_get:
        result = await _handle_search_github({"query": "rust", "max_results": 5})
    assert len(result["results"]) == 2
    r0 = result["results"][0]
    assert r0["full_name"] == "rust-lang/rust"
    assert r0["stargazers_count"] == 90000
    assert r0["engagement_score"] == 1.0  # saturates at SCALES["github_stars"]
    assert "90,000" in r0["engagement_label"]
    assert r0["source_type"] == "github"
    # No-token request still works (no Authorization header)
    sent_headers = mock_get.call_args.kwargs.get("headers", {})
    assert "Authorization" not in sent_headers


async def test_github_recency_filter_added_to_query():
    payload = {"items": []}
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json", return_value=payload) as mock_get:
        await _handle_search_github({"query": "ai", "recency_iso": "2026-05-29T00:00:00Z"})
    sent_url = mock_get.call_args.args[0]
    assert "pushed%3A%3E2026-05-29" in sent_url


async def test_github_token_added_when_env_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    payload = {"items": []}
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json", return_value=payload) as mock_get:
        await _handle_search_github({"query": "ai"})
    sent_headers = mock_get.call_args.kwargs.get("headers", {})
    assert sent_headers.get("Authorization") == "Bearer ghp_secret"


# ── register() ────────────────────────────────────────────────────────────────

def test_communities_register_exposes_all_three_tools():
    from unittest.mock import MagicMock
    from research.tools.communities import register
    registry = MagicMock()
    register(registry)
    names = [c.args[0].name for c in registry.register.call_args_list]
    assert {"search_hackernews", "search_polymarket", "search_github"} <= set(names)
