# research/tools/communities.py
"""Community-signal research tools: Hacker News, Polymarket, GitHub.

All three use free public HTTP APIs and degrade gracefully on network/auth failure.
GITHUB_TOKEN is optional and raises GitHub rate limits; without it, unauthenticated
requests are limited to ~10/minute and the tool still works for small queries.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from armature.permissions.permissions import PermissionLevel
from armature.registry.registry import ToolDescriptor, ToolRegistry

from research.tools.engagement import log_normalize, SCALES


# ── Shared HTTP + date helpers ─────────────────────────────────────────────────

def _http_get_json(url: str, headers: dict | None = None, timeout: int = 15) -> dict:
    """GET a JSON document via stdlib urllib. Raises on HTTP/network error."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _epoch_from_iso(iso: str) -> int | None:
    s = (iso or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp())
    except ValueError:
        return None


def _date_only(iso: str) -> str:
    return (iso or "").strip()[:10]


# ── Hacker News ────────────────────────────────────────────────────────────────

async def _handle_search_hackernews(args: dict[str, Any]) -> dict[str, Any]:
    """Search Hacker News via the Algolia API, scored by points and comments."""
    query = args.get("query", "").strip()
    max_results = int(args.get("max_results", 5))
    recency_iso = (args.get("recency_iso") or "").strip()

    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    params = {"query": query, "hitsPerPage": max_results}
    cutoff = _epoch_from_iso(recency_iso)
    if cutoff:
        params["numericFilters"] = f"created_at_i>{cutoff}"
    url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(params)

    try:
        data = _http_get_json(url)
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}

    results = []
    for hit in data.get("hits", [])[:max_results]:
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        results.append({
            "url": url,
            "title": hit.get("title") or "(untitled)",
            "points": points,
            "num_comments": comments,
            "author": hit.get("author") or "",
            "created_at": hit.get("created_at") or "",
            "engagement_score": round(max(
                log_normalize(points, SCALES["hn_points"]),
                log_normalize(comments, SCALES["hn_comments"]),
            ), 3),
            "engagement_label": f"▲ {points} · {comments} comments",
            "source_type": "hackernews",
        })
    return {"query": query, "results": results}


def register(registry: ToolRegistry) -> None:
    """Register community-signal tools. Populated across Tasks 3–6."""
    registry.register(ToolDescriptor(
        name="search_hackernews",
        description=(
            "Search Hacker News for stories matching a query via the Algolia API (no key). "
            "Optionally filter to stories created after an ISO-8601 cutoff. "
            "Returns {query, results: [{url, title, points, num_comments, author, "
            "created_at, engagement_score, engagement_label, source_type}], error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_search_hackernews,
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "max_results": {"type": "integer", "description": "Max results (default 5)"},
            "recency_iso": {"type": "string",  "description": "Optional ISO-8601 cutoff for created_at filter"},
        },
    ))
