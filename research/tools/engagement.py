# research/tools/engagement.py
"""Engagement metric normalization for research sources.

Maps source-native engagement metrics (upvotes, stars, volume, views, Tavily
score) to a comparable [0, 1] score via log-scaling, so the synthesizer can
weight sources by real-world signal across heterogeneous sources. The scale
constant is the native value that saturates to 1.0.
"""
from __future__ import annotations

import math

# Per-source scale = the native value that maps to ~1.0 (saturating) engagement.
SCALES: dict[str, float] = {
    "hn_points":          1500.0,
    "hn_comments":         800.0,
    "reddit_score":       5000.0,
    "reddit_comments":    2000.0,
    "github_stars":      50000.0,
    "polymarket_volume": 5_000_000.0,
    "youtube_views":    1_000_000.0,
    "web_score":            1.0,
}


def log_normalize(value: float, scale: float) -> float:
    """Log-scale a non-negative metric to [0, 1] against a source scale."""
    if value <= 0 or scale <= 0:
        return 0.0
    return min(1.0, math.log10(value + 1) / math.log10(scale + 1))