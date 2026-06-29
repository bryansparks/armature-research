# tests/tools/test_engagement.py
"""Tests for research/tools/engagement.py — engagement metric normalization."""
import math
from research.tools.engagement import log_normalize, SCALES


def test_zero_or_negative_returns_zero():
    assert log_normalize(0, 1000) == 0.0
    assert log_normalize(-5, 1000) == 0.0


def test_zero_or_negative_scale_returns_zero():
    assert log_normalize(100, 0) == 0.0
    assert log_normalize(100, -1) == 0.0


def test_value_at_scale_is_near_one():
    # value == scale should saturate at 1.0
    assert log_normalize(SCALES["github_stars"], SCALES["github_stars"]) == 1.0


def test_small_value_is_small_score():
    score = log_normalize(10, SCALES["github_stars"])
    assert 0.0 < score < 0.5


def test_large_value_clamps_to_one():
    assert log_normalize(10_000_000, SCALES["github_stars"]) == 1.0


def test_scales_cover_all_sources():
    expected = {
        "hn_points", "hn_comments", "reddit_score", "reddit_comments",
        "github_stars", "polymarket_volume", "youtube_views", "web_score",
    }
    assert expected <= set(SCALES)
    for v in SCALES.values():
        assert v > 0