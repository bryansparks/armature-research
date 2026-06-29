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
