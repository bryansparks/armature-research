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
    max_results = int(args.get("max_results") or 5)
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

    try:
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
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}


# ── Polymarket ─────────────────────────────────────────────────────────────────

def _is_number(x: Any) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def _parse_polymarket_outcomes(market: dict) -> tuple[list[str], list[float]]:
    raw = market.get("outcomes")
    if isinstance(raw, str):
        try:
            outcomes = json.loads(raw)
        except json.JSONDecodeError:
            outcomes = []
    else:
        outcomes = raw or []
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            prices = []
    else:
        prices = prices or []
    names = [str(o) for o in outcomes]
    odds = [float(p) for p in prices if _is_number(p)]
    return names, odds


def _polymarket_label(volume: float, odds: list[float]) -> str:
    if volume >= 1_000_000:
        vol = f"${volume / 1_000_000:.1f}M vol"
    elif volume > 0:
        vol = f"${volume / 1000:.0f}K vol"
    else:
        vol = "no volume"
    if odds:
        return vol + " · " + " / ".join(f"{o * 100:.0f}%" for o in odds[:2])
    return vol


async def _handle_search_polymarket(args: dict[str, Any]) -> dict[str, Any]:
    """Search Polymarket prediction markets by query substring, scored by volume."""
    query = args.get("query", "").strip()
    max_results = int(args.get("max_results") or 5)
    recency_iso = (args.get("recency_iso") or "").strip()

    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    url = (
        "https://gamma-api.polymarket.com/markets?"
        + urllib.parse.urlencode({
            "_limit": 100, "active": "true", "closed": "false",
            "order": "volumeNum", "ascending": "false",
        })
    )
    try:
        data = _http_get_json(url)
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}

    try:
        markets = data if isinstance(data, list) else (data.get("data") or data.get("markets") or [])
        needle = query.lower()
        cutoff = _date_only(recency_iso)
        results = []
        for m in markets:
            question = m.get("question") or ""
            if needle not in question.lower():
                continue
            end = (m.get("endDate") or "")[:10]
            if cutoff and end and end < cutoff:
                continue
            slug = m.get("slug") or m.get("eventSlug") or ""
            volume = float(m.get("volumeNum") or m.get("volume") or 0)
            liquidity = float(m.get("liquidityNum") or m.get("liquidity") or 0)
            outcomes, odds = _parse_polymarket_outcomes(m)
            results.append({
                "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
                "question": question or "(no question)",
                "volume": volume,
                "liquidity": liquidity,
                "outcomes": outcomes,
                "odds": odds,
                "endDate": end,
                "engagement_score": round(log_normalize(volume, SCALES["polymarket_volume"]), 3),
                "engagement_label": _polymarket_label(volume, odds),
                "source_type": "polymarket",
            })
            if len(results) >= max_results:
                break
        return {"query": query, "results": results}
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}


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
    registry.register(ToolDescriptor(
        name="search_polymarket",
        description=(
            "Search Polymarket prediction markets by query substring, scored by volume. "
            "Uses the free gamma API (no key). Optionally filter to markets ending after "
            "an ISO-8601 cutoff. Returns {query, results: [{url, question, volume, liquidity, "
            "outcomes, odds, endDate, engagement_score, engagement_label, source_type}], error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_search_polymarket,
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "max_results": {"type": "integer", "description": "Max results (default 5)"},
            "recency_iso": {"type": "string",  "description": "Optional ISO-8601 cutoff for endDate filter"},
        },
    ))
