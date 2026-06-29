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