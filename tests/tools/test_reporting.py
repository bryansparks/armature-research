"""Tests for engagement badges in the HTML report Sources panel."""
from research.tools.reporting import generate_visual_report


def _make_sources():
    return [
        {"url": "https://github.com/rust-lang/rust", "title": "Rust", "engagement_label": "★ 90,000"},
        {"url": "https://news.ycombinator.com/item?id=1", "title": "HN post"},  # no engagement_label
    ]


def test_sources_panel_renders_engagement_badge_when_present():
    html = generate_visual_report(
        question="Test topic",
        report_markdown="# Findings\n\nbody",
        sources=_make_sources(),
    )
    assert "★ 90,000" in html
    assert "sbadge" in html


def test_sources_panel_omits_badge_when_absent():
    html = generate_visual_report(
        question="Test topic",
        report_markdown="# Findings\n\nbody",
        sources=_make_sources(),
    )
    # The HN source (no engagement_label) must not introduce an empty badge span
    assert 'class="sbadge"></span>' not in html
