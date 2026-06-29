# tests/tools/test_recency.py
"""Tests for research/tools/recency.py — recency window parsing."""
import pytest
from unittest.mock import MagicMock
from research.tools.recency import parse_recency

NOW = "2026-06-28T09:48:00Z"


def test_none_returns_none():
    assert parse_recency(None, now=NOW) is None


def test_empty_returns_none():
    assert parse_recency("", now=NOW) is None
    assert parse_recency("   ", now=NOW) is None


def test_invalid_returns_none():
    assert parse_recency("banana", now=NOW) is None
    assert parse_recency("0d", now=NOW) is None


def test_days():
    r = parse_recency("30d", now=NOW)
    assert r["days"] == 30
    assert r["phrase"] == "in the last 30 days"
    assert r["iso_start"] == "2026-05-29T09:48:00Z"


def test_three_days():
    r = parse_recency("3d", now=NOW)
    assert r["days"] == 3
    assert r["iso_start"] == "2026-06-25T09:48:00Z"


def test_bare_number_is_days():
    assert parse_recency("90", now=NOW)["days"] == 90


def test_months():
    r = parse_recency("2mo", now=NOW)
    assert r["days"] == 60
    assert r["phrase"] == "in the last 2 months"


def test_single_month_phrase_uses_singular():
    r = parse_recency("1mo", now=NOW)
    assert r["phrase"] == "in the last 1 month"


def test_years():
    r = parse_recency("1y", now=NOW)
    assert r["days"] == 365
    assert r["phrase"] == "in the last 1 year"


def test_uppercase_and_whitespace_tolerated():
    r = parse_recency("  30D  ", now=NOW)
    assert r["days"] == 30


# ── parse_recency tool ─────────────────────────────────────────────────────────

async def test_tool_handler_unset_returns_zeros():
    from research.tools.recency import _handle_parse_recency
    out = await _handle_parse_recency({"recency": ""})
    assert out == {"days": 0, "phrase": "", "iso_start": ""}


async def test_tool_handler_parsed():
    from research.tools.recency import _handle_parse_recency
    out = await _handle_parse_recency({"recency": "30d", "now": NOW})
    assert out["days"] == 30
    assert out["phrase"] == "in the last 30 days"


def test_register_exposes_parse_recency_tool():
    from research.tools.recency import register
    registry = MagicMock()
    register(registry)
    names = [c.args[0].name for c in registry.register.call_args_list]
    assert "parse_recency" in names