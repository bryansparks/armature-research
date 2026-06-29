"""Tests for research/tools/web.py — web search and content extraction tools."""
import pytest
from research.tools.web import is_low_quality


# ── is_low_quality ──────────────────────────────────────────────────────────────


def test_is_low_quality_cookie_consent():
    assert is_low_quality("This site uses cookie consent technologies") is True


def test_is_low_quality_copyright_footer():
    assert is_low_quality("Copyright 2026 Acme Corp. All rights reserved.") is True


def test_is_low_quality_no_relevant_information():
    assert is_low_quality("The page does not contain relevant information") is True


def test_is_low_quality_content_insufficient():
    assert is_low_quality("The content is insufficient to answer the question") is True


def test_is_low_quality_legitimate_content():
    assert is_low_quality("The company reported $4.2B in revenue for Q1 2026") is False


def test_is_low_quality_cookie_discussion():
    """Legitimate content that discusses cookies as a topic should NOT be filtered."""
    assert is_low_quality("Browser cookie policies affect tracking accuracy") is False


def test_is_low_quality_empty_string():
    assert is_low_quality("") is True


def test_is_low_quality_none():
    assert is_low_quality(None) is True


def test_is_low_quality_non_string():
    assert is_low_quality(123) is True


def test_is_low_quality_short_bare_cookie():
    """The word 'cookie' alone is NOT a marker — only compound phrases are."""
    assert is_low_quality("I love cookie dough ice cream") is False


def test_is_low_quality_bare_copyright():
    """The word 'copyright' alone is NOT a marker — only compound phrases are."""
    assert is_low_quality("Understanding copyright law in the digital age") is False

# ── recency_days forwarding ────────────────────────────────────────────────────

async def test_web_search_forwards_recency_days_to_tavily(monkeypatch):
    from unittest.mock import MagicMock
    captured = {}
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    monkeypatch.setattr("research.tools.web._tavily_client", lambda: fake_client)
    from research.tools.web import _handle_web_search
    await _handle_web_search({"query": "ai", "recency_days": 30})
    kwargs = fake_client.search.call_args.kwargs
    assert kwargs["days"] == 30


async def test_web_search_omits_days_when_recency_unset(monkeypatch):
    from unittest.mock import MagicMock
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    monkeypatch.setattr("research.tools.web._tavily_client", lambda: fake_client)
    from research.tools.web import _handle_web_search
    await _handle_web_search({"query": "ai"})
    kwargs = fake_client.search.call_args.kwargs
    assert "days" not in kwargs
