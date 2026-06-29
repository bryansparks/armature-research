"""Tests for research/tools/manifest.py helpers."""
from research.tools.manifest import _normalize_url, _parse_list, _iter_engagement_items


def test_normalize_url_lowercases_scheme_and_host():
    assert _normalize_url("HTTPS://Example.COM/Foo") == "https://example.com/Foo"


def test_normalize_url_strips_trailing_slash_and_fragment():
    assert _normalize_url("https://example.com/foo/") == "https://example.com/foo"
    assert _normalize_url("https://example.com/foo#bar") == "https://example.com/foo"


def test_normalize_url_preserves_query():
    assert _normalize_url("https://example.com/foo?x=1&y=2") == "https://example.com/foo?x=1&y=2"


def test_normalize_url_empty_and_non_string():
    assert _normalize_url("") == ""
    assert _normalize_url(None) == ""
    assert _normalize_url(123) == ""


def test_parse_list_passes_through_actual_list():
    assert _parse_list([1, 2, 3]) == [1, 2, 3]


def test_parse_list_parses_json_string():
    assert _parse_list('[{"url": "a"}, {"url": "b"}]') == [{"url": "a"}, {"url": "b"}]


def test_parse_list_parses_python_repr_string():
    assert _parse_list("[{'url': 'a'}]") == [{"url": "a"}]


def test_parse_list_empty_and_garbage_to_empty_list():
    assert _parse_list("") == []
    assert _parse_list("   ") == []
    assert _parse_list("not a list at all") == []
    assert _parse_list('{"key": "value"}') == []  # valid JSON but not a list
    assert _parse_list(42) == []


def test_iter_engagement_items_per_query_wrapper():
    payload = [
        {"query": "rust", "results": [
            {"url": "https://example.com/1", "engagement_label": "★ 100"},
            {"url": "https://example.com/2", "engagement_label": "▲ 5"},
        ]},
    ]
    pairs = list(_iter_engagement_items(payload))
    assert ("https://example.com/1", "★ 100") in pairs
    assert ("https://example.com/2", "▲ 5") in pairs


def test_iter_engagement_items_single_dict():
    payload = {"query": "rust", "results": [
        {"url": "https://example.com/1", "engagement_label": "★ 100"},
    ]}
    assert ("https://example.com/1", "★ 100") in list(_iter_engagement_items(payload))


def test_iter_engagement_items_skips_items_without_label():
    payload = [
        {"query": "rust", "results": [
            {"url": "https://example.com/1", "engagement_label": "★ 100"},
            {"url": "https://example.com/2"},  # no engagement_label (web/Tavily)
        ]},
    ]
    pairs = list(_iter_engagement_items(payload))
    assert pairs == [("https://example.com/1", "★ 100")]


def test_iter_engagement_items_handles_flat_list():
    # YouTube transcripts arrive as a flat list of dicts with no 'results' wrapper
    # and no engagement_label -> nothing yielded.
    payload = [{"url": "https://youtube.com/watch?v=1", "video_id": "1", "transcript": "..."}]
    assert list(_iter_engagement_items(payload)) == []


# ── build_source_manifest handler ──────────────────────────────────────────────

import asyncio
from research.tools.manifest import _handle_build_source_manifest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_manifest_attaches_engagement_from_community_sources():
    args = {
        "selected_urls": '[{"url": "https://github.com/rust-lang/rust", "title": "Rust"}, '
                         '{"url": "https://news.ycombinator.com/item?id=1", "title": "HN post"}]',
        "github_results": '[{"query": "rust", "results": [{"url": "https://github.com/rust-lang/rust", '
                           '"engagement_label": "★ 90,000"}]}]',
        "hn_results": '[{"query": "rust", "results": [{"url": "https://news.ycombinator.com/item?id=1", '
                       '"engagement_label": "▲ 200 · 50 comments"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    assert result["error"] is None
    by_url = {m["url"]: m for m in result["sources_manifest"]}
    assert by_url["https://github.com/rust-lang/rust"]["engagement_label"] == "★ 90,000"
    assert by_url["https://news.ycombinator.com/item?id=1"]["engagement_label"] == "▲ 200 · 50 comments"


def test_manifest_web_source_has_no_engagement_label():
    args = {
        "selected_urls": '[{"url": "https://example.com/article", "title": "Article"}]',
        "web_results": '[{"query": "ai", "results": [{"url": "https://example.com/article", "title": "Article"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    entry = result["sources_manifest"][0]
    assert entry["url"] == "https://example.com/article"
    assert not entry["engagement_label"]  # None -> no badge (correct for web sources)


def test_manifest_normalizes_url_when_joining():
    # selected URL has a trailing slash; raw result does not — must still join.
    args = {
        "selected_urls": '[{"url": "https://github.com/rust-lang/rust/", "title": "Rust"}]',
        "github_results": '[{"query": "rust", "results": [{"url": "https://github.com/rust-lang/rust", '
                           '"engagement_label": "★ 90,000"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    assert result["sources_manifest"][0]["engagement_label"] == "★ 90,000"


def test_manifest_accumulates_and_dedups_across_rounds():
    prior = '[{"url": "https://github.com/a", "title": "A", "engagement_label": "★ 10"}, ' \
            '{"url": "https://github.com/b", "title": "B", "engagement_label": "★ 20"}]'
    args = {
        "selected_urls": '[{"url": "https://github.com/b", "title": "B"}, '
                         '{"url": "https://github.com/c", "title": "C"}]',
        "prior_manifest": prior,
        "github_results": '[{"query": "q", "results": [{"url": "https://github.com/c", '
                           '"engagement_label": "★ 30"}]}]',
    }
    result = _run(_handle_build_source_manifest(args))
    urls = [m["url"] for m in result["sources_manifest"]]
    assert urls == ["https://github.com/a", "https://github.com/b", "https://github.com/c"]
    # The dup URL keeps the prior enriched entry (engagement already attached).
    by_url = {m["url"]: m for m in result["sources_manifest"]}
    assert by_url["https://github.com/b"]["engagement_label"] == "★ 20"
    assert by_url["https://github.com/c"]["engagement_label"] == "★ 30"
    assert result["count"] == 3


def test_manifest_empty_inputs():
    result = _run(_handle_build_source_manifest({}))
    assert result["sources_manifest"] == []
    assert result["count"] == 0
    assert result["error"] is None


def test_manifest_garbled_inputs_do_not_raise():
    args = {
        "selected_urls": "not valid json or python {{{",
        "github_results": "also garbage ::",
    }
    result = _run(_handle_build_source_manifest(args))
    assert result["sources_manifest"] == []
    assert result["error"] is None  # parse failures degrade to [], not exceptions


def test_manifest_preserves_image_and_title_from_selected():
    args = {
        "selected_urls": '[{"url": "https://example.com/a", "title": "A Title", "image": "https://img/a.png"}]',
    }
    result = _run(_handle_build_source_manifest(args))
    entry = result["sources_manifest"][0]
    assert entry["title"] == "A Title"
    assert entry["image"] == "https://img/a.png"


# ── register() ────────────────────────────────────────────────────────────────

def test_manifest_register_exposes_tool():
    from unittest.mock import MagicMock
    from research.tools.manifest import register
    registry = MagicMock()
    register(registry)
    calls = registry.register.call_args_list
    names = [c.args[0].name for c in calls]
    assert "build_source_manifest" in names
    descriptor = next(c.args[0] for c in calls if c.args[0].name == "build_source_manifest")
    # Permission and parameters are set on the ToolDescriptor.
    assert descriptor.parameters is not None
    assert "selected_urls" in descriptor.parameters
    assert "prior_manifest" in descriptor.parameters
