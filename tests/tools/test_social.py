"""Tests for research/tools/social.py — Reddit + YouTube research tools."""
import pytest
from unittest.mock import patch, MagicMock


# ── _extract_video_id ─────────────────────────────────────────────────────────

def test_extract_video_id_standard_watch_url():
    from research.tools.social import _extract_video_id
    assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_watch_url_with_extra_params():
    from research.tools.social import _extract_video_id
    assert _extract_video_id("https://www.youtube.com/watch?t=42&v=dQw4w9WgXcQ&list=PL") == "dQw4w9WgXcQ"


def test_extract_video_id_short_url():
    from research.tools.social import _extract_video_id
    assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_embed_url():
    from research.tools.social import _extract_video_id
    assert _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_non_youtube_url_returns_none():
    from research.tools.social import _extract_video_id
    assert _extract_video_id("https://vimeo.com/123456789") is None


def test_extract_video_id_empty_string_returns_none():
    from research.tools.social import _extract_video_id
    assert _extract_video_id("") is None


# ── search_reddit ─────────────────────────────────────────────────────────────

async def test_search_reddit_empty_query_returns_error():
    from research.tools.social import _handle_search_reddit
    result = await _handle_search_reddit({"query": "   "})
    assert result["results"] == []
    assert "error" in result


async def test_search_reddit_missing_credentials_returns_error(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    # Simulate praw being installed so the credential check is reached
    with patch.dict("sys.modules", {"praw": MagicMock()}):
        from research.tools.social import _handle_search_reddit
        result = await _handle_search_reddit({"query": "AI agents"})
    assert result["results"] == []
    assert "REDDIT_CLIENT_ID" in result["error"]


async def test_search_reddit_client_error_returns_error_dict():
    with patch("research.tools.social._reddit_client", side_effect=RuntimeError("praw not installed")):
        from research.tools.social import _handle_search_reddit
        result = await _handle_search_reddit({"query": "AI agents"})
    assert result["results"] == []
    assert "praw" in result["error"]


async def test_search_reddit_returns_structured_results():
    sub = MagicMock()
    sub.permalink = "/r/MachineLearning/comments/abc123/discussion_post"
    sub.title = "Discussion: best AI agent frameworks"
    sub.subreddit.display_name = "MachineLearning"
    sub.score = 247
    sub.num_comments = 31
    sub.selftext = "I've been comparing Armature and LangChain..."
    sub.created_utc = 1700000000.0

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.search.return_value = [sub]

    with patch("research.tools.social._reddit_client", return_value=mock_reddit):
        from research.tools.social import _handle_search_reddit
        result = await _handle_search_reddit({"query": "AI agent frameworks", "max_results": 5})

    assert len(result["results"]) == 1
    r = result["results"][0]
    assert r["url"] == "https://reddit.com/r/MachineLearning/comments/abc123/discussion_post"
    assert r["title"] == "Discussion: best AI agent frameworks"
    assert r["subreddit"] == "MachineLearning"
    assert r["score"] == 247
    assert "Armature" in r["snippet"]


async def test_search_reddit_api_exception_returns_error_dict():
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.search.side_effect = Exception("rate limited")

    with patch("research.tools.social._reddit_client", return_value=mock_reddit):
        from research.tools.social import _handle_search_reddit
        result = await _handle_search_reddit({"query": "test"})

    assert result["results"] == []
    assert "rate limited" in result["error"]


# ── search_youtube_videos ─────────────────────────────────────────────────────

async def test_search_youtube_videos_empty_queries_returns_error():
    from research.tools.social import _handle_search_youtube_videos
    result = await _handle_search_youtube_videos({"queries": []})
    assert result["videos"] == []
    assert "error" in result


async def test_search_youtube_videos_accepts_string_queries():
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "Test Video", "content": "snippet"},
        ]
    }
    with patch("research.tools.social._tavily_client", return_value=mock_client):
        from research.tools.social import _handle_search_youtube_videos
        result = await _handle_search_youtube_videos({"queries": ["AI agents tutorial"]})
    assert len(result["videos"]) == 1
    assert result["videos"][0]["video_id"] == "dQw4w9WgXcQ"


async def test_search_youtube_videos_accepts_query_objects():
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "Test Video", "content": ""},
        ]
    }
    with patch("research.tools.social._tavily_client", return_value=mock_client):
        from research.tools.social import _handle_search_youtube_videos
        result = await _handle_search_youtube_videos({
            "queries": [{"query": "AI agents tutorial", "intent": "learn", "sub_question_index": 1}],
        })
    assert len(result["videos"]) == 1
    assert result["videos"][0]["video_id"] == "dQw4w9WgXcQ"


async def test_search_youtube_videos_deduplicates_by_video_id():
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "Same Video", "content": ""},
        ]
    }
    with patch("research.tools.social._tavily_client", return_value=mock_client):
        from research.tools.social import _handle_search_youtube_videos
        result = await _handle_search_youtube_videos({"queries": ["query one", "query two"]})
    assert len(result["videos"]) == 1


async def test_search_youtube_videos_filters_non_youtube_urls():
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"url": "https://vimeo.com/12345", "title": "Vimeo Video", "content": ""},
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "YouTube Video", "content": ""},
        ]
    }
    with patch("research.tools.social._tavily_client", return_value=mock_client):
        from research.tools.social import _handle_search_youtube_videos
        result = await _handle_search_youtube_videos({"queries": ["test query"]})
    assert len(result["videos"]) == 1
    assert result["videos"][0]["video_id"] == "dQw4w9WgXcQ"


async def test_search_youtube_videos_respects_max_total():
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"url": f"https://youtube.com/watch?v=vid{i:011d}", "title": f"Video {i}", "content": ""}
            for i in range(5)
        ]
    }
    with patch("research.tools.social._tavily_client", return_value=mock_client):
        from research.tools.social import _handle_search_youtube_videos
        result = await _handle_search_youtube_videos({
            "queries": ["q1", "q2", "q3"],
            "max_total": 3,
        })
    assert len(result["videos"]) <= 3


async def test_search_youtube_videos_tavily_error_returns_error_dict():
    with patch("research.tools.social._tavily_client", side_effect=RuntimeError("TAVILY_API_KEY not set")):
        from research.tools.social import _handle_search_youtube_videos
        result = await _handle_search_youtube_videos({"queries": ["test"]})
    assert result["videos"] == []
    assert "TAVILY_API_KEY" in result["error"]


# ── fetch_youtube_transcript ──────────────────────────────────────────────────

async def test_fetch_youtube_transcript_empty_url():
    from research.tools.social import _handle_fetch_youtube_transcript
    result = await _handle_fetch_youtube_transcript({"url": ""})
    assert result["transcript"] == ""
    assert "error" in result


async def test_fetch_youtube_transcript_non_youtube_url():
    from research.tools.social import _handle_fetch_youtube_transcript
    result = await _handle_fetch_youtube_transcript({"url": "https://vimeo.com/12345"})
    assert result["transcript"] == ""
    assert "video ID" in result["error"]


async def test_fetch_youtube_transcript_package_not_installed():
    with patch("research.tools.social._get_transcript", side_effect=ImportError("No module named 'youtube_transcript_api'")):
        from research.tools.social import _handle_fetch_youtube_transcript
        result = await _handle_fetch_youtube_transcript(
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}
        )
    assert result["transcript"] == ""
    assert "youtube-transcript-api" in result["error"]


async def test_fetch_youtube_transcript_returns_joined_segments():
    segments = [
        {"text": "Hello everyone", "start": 0.0, "duration": 1.5},
        {"text": "welcome to this talk", "start": 1.5, "duration": 2.0},
        {"text": "about AI agents", "start": 3.5, "duration": 1.0},
    ]
    with patch("research.tools.social._get_transcript", return_value=segments):
        from research.tools.social import _handle_fetch_youtube_transcript
        result = await _handle_fetch_youtube_transcript(
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}
        )
    assert "Hello everyone" in result["transcript"]
    assert "about AI agents" in result["transcript"]
    assert result["video_id"] == "dQw4w9WgXcQ"
    assert result["total_chars"] > 0


async def test_fetch_youtube_transcript_respects_max_chars():
    segments = [{"text": "x" * 100, "start": float(i), "duration": 1.0} for i in range(200)]
    with patch("research.tools.social._get_transcript", return_value=segments):
        from research.tools.social import _handle_fetch_youtube_transcript
        result = await _handle_fetch_youtube_transcript(
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "max_chars": 500}
        )
    assert len(result["transcript"]) <= 500
    assert result["total_chars"] > 500


async def test_fetch_youtube_transcript_api_error_returns_error_dict():
    with patch("research.tools.social._get_transcript", side_effect=Exception("TranscriptsDisabled")):
        from research.tools.social import _handle_fetch_youtube_transcript
        result = await _handle_fetch_youtube_transcript(
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}
        )
    assert result["transcript"] == ""
    assert "TranscriptsDisabled" in result["error"]


# ── recency + engagement ───────────────────────────────────────────────────────

def test_reddit_time_filter_mapping():
    from research.tools.social import _reddit_time_filter
    assert _reddit_time_filter(1) == "day"
    assert _reddit_time_filter(7) == "week"
    assert _reddit_time_filter(30) == "month"
    assert _reddit_time_filter(365) == "year"
    assert _reddit_time_filter(None) == "all"
    assert _reddit_time_filter("garbage") == "all"


async def test_search_reddit_passes_time_filter(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    reddit = MagicMock()
    subreddit = MagicMock()
    subreddit.search.return_value = iter([])
    reddit.subreddit.return_value = subreddit
    with patch("research.tools.social._reddit_client", return_value=reddit):
        from research.tools.social import _handle_search_reddit
        await _handle_search_reddit({"query": "ai", "recency_days": 30})
    assert subreddit.search.call_args.kwargs.get("time_filter") == "month"


async def test_search_reddit_omits_time_filter_when_unset(monkeypatch):
    from unittest.mock import MagicMock
    reddit = MagicMock()
    subreddit = MagicMock()
    subreddit.search.return_value = iter([])
    reddit.subreddit.return_value = subreddit
    with patch("research.tools.social._reddit_client", return_value=reddit):
        from research.tools.social import _handle_search_reddit
        await _handle_search_reddit({"query": "ai"})
    assert "time_filter" not in subreddit.search.call_args.kwargs


async def test_search_reddit_attaches_engagement_fields():
    from unittest.mock import MagicMock
    sub = MagicMock()
    sub.permalink = "/r/x/comments/1/p"
    sub.title = "T"
    sub.subreddit.display_name = "x"
    sub.score = 1200
    sub.num_comments = 90
    sub.selftext = ""
    sub.created_utc = 1700000000
    subreddit = MagicMock()
    subreddit.search.return_value = iter([sub])
    reddit = MagicMock()
    reddit.subreddit.return_value = subreddit
    with patch("research.tools.social._reddit_client", return_value=reddit):
        from research.tools.social import _handle_search_reddit
        result = await _handle_search_reddit({"query": "ai"})
    r = result["results"][0]
    assert r["source_type"] == "reddit"
    assert 0.0 < r["engagement_score"] <= 1.0
    assert "1200" in r["engagement_label"]


async def test_search_youtube_forwards_recency_days(monkeypatch):
    from unittest.mock import MagicMock
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    monkeypatch.setattr("research.tools.social._tavily_client", lambda: fake_client)
    from research.tools.social import _handle_search_youtube_videos
    await _handle_search_youtube_videos({"queries": ["ai"], "recency_days": 30})
    assert fake_client.search.call_args.kwargs.get("days") == 30
