"""Social media and video research tools.

Exposes three tools to the Armature workflow:
  search_reddit            — search Reddit posts and discussions via PRAW
  search_youtube_videos    — find YouTube video URLs via Tavily web search
  fetch_youtube_transcript — extract transcript from a YouTube video (no API key needed)

Optional setup — tools degrade gracefully if not configured:
  Reddit:  pip install praw  (or: pip install 'research-analyst[social]')
           Set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET in .env
           Create a Reddit app at: https://www.reddit.com/prefs/apps
           Choose "script" app type; redirect URI can be http://localhost:8080

  YouTube: pip install youtube-transcript-api  (or: pip install 'research-analyst[social]')
           No API key required — works on any public video with captions enabled.
           search_youtube_videos reuses the existing TAVILY_API_KEY for video discovery.

Why Reddit for research:
  Authentic user voices unfiltered by PR: real product experiences, honest comparisons,
  pricing complaints, workarounds, and community consensus that formal sources omit.

Why YouTube for research:
  Conference keynotes, product demos, founder interviews, and tutorial content reveal
  how products actually work and what teams are emphasizing in a way written docs don't.
"""
from __future__ import annotations

import os
import re
from typing import Any

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    pass

from armature.permissions.permissions import PermissionLevel
from armature.registry.registry import ToolDescriptor, ToolRegistry


# ── Reddit client factory ─────────────────────────────────────────────────────

def _reddit_client():
    """Return a PRAW Reddit instance, raising clearly if package or keys are missing."""
    try:
        import praw
    except ImportError as exc:
        raise RuntimeError(
            "praw is not installed. "
            "Run: pip install praw  (or: pip install 'research-analyst[social]')"
        ) from exc
    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are not set. "
            "Create a Reddit app at https://www.reddit.com/prefs/apps and add both to your .env"
        )
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent="armature-research-analyst/1.0 (contact: research@example.com)",
    )


# ── Tavily client (shared with web.py, local copy avoids coupling) ────────────

def _tavily_client():
    """Return a TavilyClient, raising clearly if package or key is missing."""
    try:
        from tavily import TavilyClient
    except ImportError as exc:
        raise RuntimeError(
            "tavily-python is not installed. Run: pip install tavily-python"
        ) from exc
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY environment variable is not set. "
            "Get a key at https://app.tavily.com"
        )
    return TavilyClient(api_key=api_key)


# ── YouTube helpers ───────────────────────────────────────────────────────────

def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from standard URL formats."""
    for pattern in (
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _get_transcript(video_id: str) -> list[dict]:
    """Fetch YouTube transcript segments. Separated for testability."""
    from youtube_transcript_api import YouTubeTranscriptApi
    return YouTubeTranscriptApi.get_transcript(video_id)


# ── Reddit search ─────────────────────────────────────────────────────────────

async def _handle_search_reddit(args: dict[str, Any]) -> dict[str, Any]:
    """Search Reddit for posts matching a query via PRAW."""
    query = args.get("query", "").strip()
    subreddits = args.get("subreddits", "all")
    max_results = int(args.get("max_results", 5))
    sort = args.get("sort", "relevance")

    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    try:
        reddit = _reddit_client()
    except RuntimeError as exc:
        return {"query": query, "results": [], "error": str(exc)}

    try:
        results = []
        for submission in reddit.subreddit(subreddits).search(query, limit=max_results, sort=sort):
            results.append({
                "url": f"https://reddit.com{submission.permalink}",
                "title": submission.title,
                "subreddit": submission.subreddit.display_name,
                "score": submission.score,
                "num_comments": submission.num_comments,
                "snippet": (submission.selftext or "")[:500],
                "created_utc": int(submission.created_utc),
            })
        return {"query": query, "subreddits": subreddits, "results": results}
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}


# ── YouTube video search ──────────────────────────────────────────────────────

async def _handle_search_youtube_videos(args: dict[str, Any]) -> dict[str, Any]:
    """Find YouTube video URLs by running Tavily searches with site:youtube.com."""
    raw_queries = args.get("queries", [])
    max_per_query = int(args.get("max_per_query", 3))
    max_total = int(args.get("max_total", 8))

    if not raw_queries:
        return {"videos": [], "error": "empty queries list"}

    # Accept either plain strings or query objects (e.g. from plan_searches.queries)
    query_strings = [
        q["query"] if isinstance(q, dict) else str(q)
        for q in raw_queries
    ]

    try:
        client = _tavily_client()
    except RuntimeError as exc:
        return {"videos": [], "error": str(exc)}

    seen_ids: set[str] = set()
    videos: list[dict] = []

    for q in query_strings:
        if len(videos) >= max_total:
            break
        try:
            response = client.search(
                query=f"{q} site:youtube.com",
                max_results=max_per_query,
                search_depth="basic",
                include_answer=False,
            )
            for r in response.get("results", []):
                url = r.get("url", "")
                video_id = _extract_video_id(url)
                if not video_id or video_id in seen_ids:
                    continue
                seen_ids.add(video_id)
                videos.append({
                    "url": url,
                    "video_id": video_id,
                    "title": r.get("title", url),
                    "snippet": r.get("content", ""),
                })
                if len(videos) >= max_total:
                    break
        except Exception:
            continue

    return {"videos": videos}


# ── YouTube transcript fetch ──────────────────────────────────────────────────

async def _handle_fetch_youtube_transcript(args: dict[str, Any]) -> dict[str, Any]:
    """Extract the auto-generated transcript from a YouTube video."""
    url = args.get("url", "").strip()
    max_chars = int(args.get("max_chars", 8000))

    if not url:
        return {"url": url, "video_id": None, "transcript": "", "error": "empty url"}

    video_id = _extract_video_id(url)
    if not video_id:
        return {
            "url": url,
            "video_id": None,
            "transcript": "",
            "error": f"could not extract video ID from URL: {url}",
        }

    try:
        segments = _get_transcript(video_id)
        full_text = " ".join(seg["text"] for seg in segments)
        return {
            "url": url,
            "video_id": video_id,
            "transcript": full_text[:max_chars],
            "total_chars": len(full_text),
        }
    except ImportError:
        return {
            "url": url,
            "video_id": video_id,
            "transcript": "",
            "error": (
                "youtube-transcript-api is not installed. "
                "Run: pip install youtube-transcript-api  "
                "(or: pip install 'research-analyst[social]')"
            ),
        }
    except Exception as exc:
        return {"url": url, "video_id": video_id, "transcript": "", "error": str(exc)}


# ── Registration ──────────────────────────────────────────────────────────────

def register(registry: ToolRegistry) -> None:
    registry.register(ToolDescriptor(
        name="search_reddit",
        description=(
            "Search Reddit for posts and discussions matching a query via PRAW. "
            "Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (create a Reddit app at "
            "https://www.reddit.com/prefs/apps). Degrades gracefully if not configured. "
            "Returns {query, subreddits, results: [{url, title, subreddit, score, "
            "num_comments, snippet, created_utc}], error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_search_reddit,
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "subreddits":  {"type": "string",  "description": "Subreddit(s) to search (default: 'all')"},
            "max_results": {"type": "integer", "description": "Maximum posts to return (default 5)"},
            "sort":        {"type": "string",  "description": "Sort: relevance|hot|new|top (default: relevance)"},
        },
    ))
    registry.register(ToolDescriptor(
        name="search_youtube_videos",
        description=(
            "Find YouTube video URLs by searching with site:youtube.com via Tavily. "
            "Accepts a list of query strings or plan_searches query objects. "
            "Deduplicates by video ID. Requires TAVILY_API_KEY. "
            "Returns {videos: [{url, video_id, title, snippet}], error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_search_youtube_videos,
        parameters={
            "queries":       {"type": "array",   "description": "List of query strings or objects with a 'query' field"},
            "max_per_query": {"type": "integer", "description": "Max videos per query (default 3)"},
            "max_total":     {"type": "integer", "description": "Max total videos returned (default 8)"},
        },
    ))
    registry.register(ToolDescriptor(
        name="fetch_youtube_transcript",
        description=(
            "Extract the auto-generated transcript from a YouTube video URL. "
            "No API key required. Requires: pip install youtube-transcript-api. "
            "Works on any public video with captions. Handles watch, youtu.be, and embed URLs. "
            "Returns {url, video_id, transcript, total_chars, error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_fetch_youtube_transcript,
        parameters={
            "url":       {"type": "string",  "description": "YouTube video URL (any standard format)"},
            "max_chars": {"type": "integer", "description": "Max transcript characters to return (default 8000)"},
        },
    ))
