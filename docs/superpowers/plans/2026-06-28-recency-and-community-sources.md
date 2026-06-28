# Recency Option + Community Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--input recency=30d` option (hard API filters + soft prompt phrasing), three new free-API community sources (Hacker News, Polymarket, GitHub), and engagement-weighted ranking to the Research-Analyst workflow.

**Architecture:** Two new focused tool modules — `research/tools/recency.py` (pure parser exposed as a `parse_recency` tool) and `research/tools/communities.py` (HN/Polymarket/GitHub handlers) — plus a tiny shared `research/tools/engagement.py` normalizer. Existing `web.py`/`social.py` gain an optional `recency_days` arg. The workflow declares a `recency` input, runs a `prepare_recency` stage that parses it, threads the parsed `{days, iso_start, phrase}` into the search `tool_call`s and prompt phrasing, and fans out three new search stages alongside the existing web/reddit/youtube searches into `select_sources`.

**Tech Stack:** Python 3.11+, Armature workflow YAML, Tavily (web), PRAW (Reddit), stdlib `urllib`/`json` (HN/Polymarket/GitHub — no new deps), PyYAML (structural tests, already a transitive dep via armature), pytest + pytest-asyncio.

## Global Constraints

- **No new runtime dependencies.** HN/Polymarket/GitHub use stdlib `urllib.request` + `json` only. Do not add `requests`/`httpx` to `pyproject.toml`.
- **Graceful degradation is the contract.** Every new/modified tool returns `{…, results: [], error: str(exc)}` on any failure (network, missing optional `GITHUB_TOKEN`, bad API response). Never raise. The workflow must continue with the remaining sources.
- **Recency is optional.** When `recency` is unset/invalid, behavior is byte-for-byte identical to today. Invalid `recency` is treated as unset with a logged warning — never a hard failure.
- **All tool handlers are `async def`** and follow the existing `register(registry)` + `ToolDescriptor` pattern from `research/tools/social.py`. Imports of `PermissionLevel`/`ToolDescriptor`/`ToolRegistry` come from `armature.permissions.permissions` and `armature.registry.registry`.
- **TDD.** Write the failing test first, run it red, implement, run green, commit. Frequent commits per task.
- **Engagement fields are additive.** New result dicts add `engagement_score`, `engagement_label`, and `source_type` alongside existing fields. Existing consumers ignore unknown keys, so this is non-breaking.
- **`competitive-intel.yaml` is out of scope** for this plan (deferred follow-on).

**Spec:** `docs/superpowers/specs/2026-06-28-recency-and-community-sources-design.md`

---

## File Structure

- **Create** `research/tools/engagement.py` — log-scale normalizer + per-source scale constants. Single responsibility: comparable engagement scoring across heterogeneous sources.
- **Create** `research/tools/recency.py` — pure `parse_recency()` parser + `register()` exposing it as a `parse_recency` tool. No I/O.
- **Create** `research/tools/communities.py` — `search_hackernews`, `search_polymarket`, `search_github` handlers + `_http_get_json` helper + `register()`.
- **Modify** `research/tools/web.py` — `web_search` accepts `recency_days` → Tavily `days` kwarg; add to schema.
- **Modify** `research/tools/social.py` — `search_reddit` accepts `recency_days` → PRAW `time_filter` + engagement fields; `search_youtube_videos` accepts `recency_days` → Tavily `days`; import normalizer.
- **Modify** `research/tools/reporting.py` — render `engagement_label` badge in the Sources panel (additive).
- **Modify** `workflows/research-analyst.yaml` — declare `recency` input.
- **Modify** `workflows/research-round.yaml` — declare `recency` input, register `recency` + `communities` modules, add `prepare_recency` + three search stages, update `select_sources.depends_on` + prompt context.
- **Create** `tests/tools/test_engagement.py`, `tests/tools/test_recency.py`, `tests/tools/test_communities.py`, `tests/workflows/__init__.py`, `tests/workflows/test_structure.py`. Modify `tests/tools/test_web.py`, `tests/tools/test_social.py`.

---

## Task 1: Engagement normalizer

**Files:**
- Create: `research/tools/engagement.py`
- Test: `tests/tools/test_engagement.py`

**Interfaces:**
- Produces: `log_normalize(value: float, scale: float) -> float` and `SCALES: dict[str, float]`. Consumed by Tasks 3–5 (communities) and Task 8 (social).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_engagement.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.tools.engagement'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_engagement.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add research/tools/engagement.py tests/tools/test_engagement.py
git commit -m "feat: add engagement metric normalizer (log-scaled, per-source)"
```

---

## Task 2: Recency parser + parse_recency tool

**Files:**
- Create: `research/tools/recency.py`
- Test: `tests/tools/test_recency.py`

**Interfaces:**
- Produces: `parse_recency(s: str | None, now: str | None = None) -> dict | None` returning `{days: int, phrase: str, iso_start: str}` (or `None`); `register(registry)` exposing the `parse_recency` tool whose handler returns `{days, phrase, iso_start}` with zeros/empty strings when unset. Consumed by Task 9 (`prepare_recency` stage) and indirectly by Tasks 7–8 (which receive the already-parsed `recency_days`/`recency_iso` from the workflow).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_recency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.tools.recency'`

- [ ] **Step 3: Write minimal implementation**

```python
# research/tools/recency.py
"""Recency window parser for the research workflow.

Parses a human recency string (e.g. "30d", "3d", "2mo", "1y") into a structured
window used by search tools (hard filters) and workflow prompts (soft phrasing).
Pure functions only — no I/O. Also exposes a `parse_recency` tool so the workflow
can parse the raw `recency` input into structured values via a `prepare_recency`
stage.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from armature.permissions.permissions import PermissionLevel
from armature.registry.registry import ToolDescriptor, ToolRegistry

_UNIT_DAYS = {"d": 1, "mo": 30, "y": 365}


def parse_recency(s: str | None, now: str | None = None) -> dict | None:
    """Parse a recency string into {days, phrase, iso_start}, or None if unset/invalid.

    Accepts: "30d", "3d", "2mo", "1y", bare "90" (days). "" or None -> None.
    now: optional ISO-8601 timestamp for a deterministic cutoff; if absent/empty,
        the current UTC time is used.
    """
    if not s or not s.strip():
        return None
    text = s.strip().lower()
    m = re.fullmatch(r"(\d+)\s*(d|mo|y)?", text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2) or "d"
    days = n * _UNIT_DAYS[unit]
    if days <= 0:
        return None
    base = _parse_now(now)
    cutoff = base - timedelta(days=days)
    if unit == "d":
        phrase = f"in the last {days} days"
    elif unit == "mo":
        phrase = f"in the last {n} month{'s' if n != 1 else ''}"
    else:
        phrase = f"in the last {n} year{'s' if n != 1 else ''}"
    return {
        "days": days,
        "phrase": phrase,
        "iso_start": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _parse_now(now: str | None) -> datetime:
    if now:
        s = now.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def _handle_parse_recency(args: dict[str, Any]) -> dict[str, Any]:
    """Tool handler: parse the raw recency input into structured values."""
    parsed = parse_recency(args.get("recency"), args.get("now"))
    if parsed is None:
        return {"days": 0, "phrase": "", "iso_start": ""}
    return parsed


def register(registry: ToolRegistry) -> None:
    registry.register(ToolDescriptor(
        name="parse_recency",
        description=(
            "Parse a recency window string (e.g. '30d', '3d', '2mo', '1y') into "
            "{days, phrase, iso_start}. Returns zero/empty values when input is unset "
            "or invalid. phrase is a human wording like 'in the last 30 days'."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_parse_recency,
        parameters={
            "recency": {"type": "string", "description": "Recency window, e.g. '30d'. Empty/invalid -> unset."},
            "now":     {"type": "string", "description": "Optional ISO-8601 'now' for a deterministic cutoff."},
        },
    ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_recency.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add research/tools/recency.py tests/tools/test_recency.py
git commit -m "feat: add recency window parser + parse_recency tool"
```

---

## Task 3: search_hackernews

**Files:**
- Create: `research/tools/communities.py` (skeleton + HN handler + helpers + register placeholder)
- Test: `tests/tools/test_communities.py`

**Interfaces:**
- Produces: `_handle_search_hackernews(args) -> {query, results: [...], error?}`, where each result has `url, title, points, num_comments, author, created_at, engagement_score, engagement_label, source_type`. Also `_http_get_json(url, headers?, timeout?) -> dict` (patchable in tests).
- Consumes: `from research.tools.engagement import log_normalize, SCALES` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_communities.py
"""Tests for research/tools/communities.py — Hacker News, Polymarket, GitHub."""
import pytest
from unittest.mock import patch


# ── search_hackernews ─────────────────────────────────────────────────────────

async def test_hn_empty_query_returns_error():
    from research.tools.communities import _handle_search_hackernews
    result = await _handle_search_hackernews({"query": "   "})
    assert result["results"] == []
    assert "error" in result


async def test_hn_http_failure_degrades_gracefully():
    from research.tools.communities import _handle_search_hackernews
    with patch("research.tools.communities._http_get_json", side_effect=RuntimeError("boom")):
        result = await _handle_search_hackernews({"query": "rust"})
    assert result["results"] == []
    assert "boom" in result["error"]


async def test_hn_returns_structured_results():
    payload = {"hits": [
        {"objectID": "1", "title": "Rust is great", "points": 200, "num_comments": 50,
         "author": "alice", "created_at": "2026-06-01T00:00:00Z", "url": "https://example.com/1"},
        {"objectID": "2", "title": "Self post", "points": 0, "num_comments": 3,
         "author": "bob", "created_at": "2026-06-02T00:00:00Z", "url": None},
    ]}
    from research.tools.communities import _handle_search_hackernews
    with patch("research.tools.communities._http_get_json", return_value=payload):
        result = await _handle_search_hackernews({"query": "rust", "max_results": 5})
    assert result["query"] == "rust"
    assert len(result["results"]) == 2
    r0 = result["results"][0]
    assert r0["url"] == "https://example.com/1"
    assert r0["points"] == 200
    assert r0["source_type"] == "hackernews"
    assert 0.0 < r0["engagement_score"] <= 1.0
    assert "200" in r0["engagement_label"]
    # Null url falls back to the HN item URL
    assert result["results"][1]["url"] == "https://news.ycombinator.com/item?id=2"


async def test_hn_recency_filter_added_to_request():
    from research.tools.communities import _handle_search_hackernews
    captured = {}
    def fake_get(url, headers=None, timeout=15):
        captured["url"] = url
        return {"hits": []}
    with patch("research.tools.communities._http_get_json", side_effect=fake_get):
        await _handle_search_hackernews({"query": "ai", "recency_iso": "2026-05-29T00:00:00Z"})
    assert "numericFilters=created_at_i" in captured["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_communities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.tools.communities'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_communities.py -v`
Expected: PASS (4 HN tests)

- [ ] **Step 5: Commit**

```bash
git add research/tools/communities.py tests/tools/test_communities.py
git commit -m "feat: add search_hackernews tool (Algolia API)"
```

---

## Task 4: search_polymarket

**Files:**
- Modify: `research/tools/communities.py` (add handler + helpers, register the tool)
- Test: `tests/tools/test_communities.py` (append tests)

**Interfaces:**
- Produces: `_handle_search_polymarket(args) -> {query, results: [...], error?}`; each result has `url, question, volume, liquidity, outcomes, odds, endDate, engagement_score, engagement_label, source_type`.

- [ ] **Step 1: Write the failing test** (append to `tests/tools/test_communities.py`)

```python
# ── search_polymarket ──────────────────────────────────────────────────────────

async def test_polymarket_empty_query_returns_error():
    from research.tools.communities import _handle_search_polymarket
    result = await _handle_search_polymarket({"query": ""})
    assert result["results"] == []
    assert "error" in result


async def test_polymarket_http_failure_degrades_gracefully():
    from research.tools.communities import _handle_search_polymarket
    with patch("research.tools.communities._http_get_json", side_effect=RuntimeError("network")):
        result = await _handle_search_polymarket({"query": "election"})
    assert result["results"] == []
    assert "network" in result["error"]


async def test_polymarket_filters_by_query_and_returns_results():
    payload = [
        {"slug": "will-x-happen", "question": "Will the election happen in 2026?",
         "volumeNum": 2_500_000, "liquidityNum": 100000,
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.68", "0.32"]',
         "endDate": "2026-12-31T00:00:00Z"},
        {"slug": "unrelated", "question": "Will it snow?", "volumeNum": 10,
         "outcomes": "[]", "outcomePrices": "[]", "endDate": ""},
    ]
    from research.tools.communities import _handle_search_polymarket
    with patch("research.tools.communities._http_get_json", return_value=payload):
        result = await _handle_search_polymarket({"query": "election", "max_results": 5})
    assert len(result["results"]) == 1
    m = result["results"][0]
    assert m["question"].startswith("Will the election")
    assert m["volume"] == 2_500_000
    assert m["outcomes"] == ["Yes", "No"]
    assert m["odds"] == [0.68, 0.32]
    assert m["source_type"] == "polymarket"
    assert 0.0 < m["engagement_score"] <= 1.0
    assert "68%" in m["engagement_label"]


async def test_polymarket_recency_filter_excludes_old_markets():
    payload = [
        {"slug": "a", "question": "election recent", "volumeNum": 1_000_000,
         "outcomes": "[]", "outcomePrices": "[]", "endDate": "2026-06-15T00:00:00Z"},
        {"slug": "b", "question": "election old", "volumeNum": 1_000_000,
         "outcomes": "[]", "outcomePrices": "[]", "endDate": "2026-01-01T00:00:00Z"},
    ]
    from research.tools.communities import _handle_search_polymarket
    with patch("research.tools.communities._http_get_json", return_value=payload):
        result = await _handle_search_polymarket(
            {"query": "election", "recency_iso": "2026-06-01T00:00:00Z"})
    end_dates = [r["endDate"] for r in result["results"]]
    assert "2026-01-01" not in end_dates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_communities.py -k polymarket -v`
Expected: FAIL with `ImportError: cannot import name '_handle_search_polymarket'`

- [ ] **Step 3: Write minimal implementation** (insert into `research/tools/communities.py` before `register`)

```python
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
    max_results = int(args.get("max_results", 5))
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
```

And register it — add this `ToolDescriptor` inside `register()` in `research/tools/communities.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_communities.py -k polymarket -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/tools/communities.py tests/tools/test_communities.py
git commit -m "feat: add search_polymarket tool (gamma API)"
```

---

## Task 5: search_github

**Files:**
- Modify: `research/tools/communities.py` (add handler, register the tool)
- Test: `tests/tools/test_communities.py` (append tests)

**Interfaces:**
- Produces: `_handle_search_github(args) -> {query, results: [...], error?}`; each result has `url, full_name, description, stargazers_count, language, updated_at, engagement_score, engagement_label, source_type`.

- [ ] **Step 1: Write the failing test** (append to `tests/tools/test_communities.py`)

```python
# ── search_github ──────────────────────────────────────────────────────────────

async def test_github_empty_query_returns_error():
    from research.tools.communities import _handle_search_github
    result = await _handle_search_github({"query": ""})
    assert result["results"] == []
    assert "error" in result


async def test_github_http_error_degrades_gracefully():
    import urllib.error
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json",
               side_effect=urllib.error.HTTPError("u", 403, "Forbidden", {}, None)):
        result = await _handle_search_github({"query": "rust"})
    assert result["results"] == []
    assert "403" in result["error"]


async def test_github_returns_structured_results():
    payload = {"items": [
        {"full_name": "rust-lang/rust", "html_url": "https://github.com/rust-lang/rust",
         "description": "A language", "stargazers_count": 90000, "language": "Rust",
         "updated_at": "2026-06-20T00:00:00Z"},
        {"full_name": "other/repo", "html_url": "https://github.com/other/repo",
         "description": None, "stargazers_count": 0, "language": None,
         "updated_at": "2026-06-21T00:00:00Z"},
    ]}
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json", return_value=payload) as mock_get:
        result = await _handle_search_github({"query": "rust", "max_results": 5})
    assert len(result["results"]) == 2
    r0 = result["results"][0]
    assert r0["full_name"] == "rust-lang/rust"
    assert r0["stargazers_count"] == 90000
    assert r0["engagement_score"] == 1.0  # saturates at SCALES["github_stars"]
    assert "90,000" in r0["engagement_label"]
    assert r0["source_type"] == "github"
    # No-token request still works (no Authorization header)
    sent_headers = mock_get.call_args.kwargs.get("headers", {})
    assert "Authorization" not in sent_headers


async def test_github_recency_filter_added_to_query():
    payload = {"items": []}
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json", return_value=payload) as mock_get:
        await _handle_search_github({"query": "ai", "recency_iso": "2026-05-29T00:00:00Z"})
    sent_url = mock_get.call_args.args[0]
    assert "pushed%3A%3E2026-05-29" in sent_url


async def test_github_token_added_when_env_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    payload = {"items": []}
    from research.tools.communities import _handle_search_github
    with patch("research.tools.communities._http_get_json", return_value=payload) as mock_get:
        await _handle_search_github({"query": "ai"})
    sent_headers = mock_get.call_args.kwargs.get("headers", {})
    assert sent_headers.get("Authorization") == "Bearer ghp_secret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_communities.py -k github -v`
Expected: FAIL with `ImportError: cannot import name '_handle_search_github'`

- [ ] **Step 3: Write minimal implementation** (insert into `research/tools/communities.py` before `register`)

```python
# ── GitHub ─────────────────────────────────────────────────────────────────────

async def _handle_search_github(args: dict[str, Any]) -> dict[str, Any]:
    """Search GitHub repositories, scored by stars. GITHUB_TOKEN is optional."""
    query = args.get("query", "").strip()
    max_results = int(args.get("max_results", 5))
    recency_iso = (args.get("recency_iso") or "").strip()

    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    q = f"{query} pushed:>{_date_only(recency_iso)}" if recency_iso else query
    url = (
        "https://api.github.com/search/repositories?"
        + urllib.parse.urlencode({"q": q, "sort": "stars", "order": "desc", "per_page": max_results})
    )
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        data = _http_get_json(url, headers=headers)
    except urllib.error.HTTPError as exc:
        return {"query": query, "results": [], "error": f"github api {exc.code}"}
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}

    results = []
    for repo in data.get("items", [])[:max_results]:
        stars = int(repo.get("stargazers_count") or 0)
        results.append({
            "url": repo.get("html_url") or "",
            "full_name": repo.get("full_name") or "",
            "description": repo.get("description") or "",
            "stargazers_count": stars,
            "language": repo.get("language") or "",
            "updated_at": repo.get("updated_at") or "",
            "engagement_score": round(log_normalize(stars, SCALES["github_stars"]), 3),
            "engagement_label": f"★ {stars:,}" if stars else "★ 0",
            "source_type": "github",
        })
    return {"query": query, "results": results}
```

Register it — add this `ToolDescriptor` inside `register()` in `research/tools/communities.py`:

```python
    registry.register(ToolDescriptor(
        name="search_github",
        description=(
            "Search GitHub repositories matching a query, scored by stars. Uses the free "
            "GitHub Search API; set GITHUB_TOKEN to raise rate limits (unauthenticated is "
            "~10 req/min). Optionally filter to repos pushed after an ISO-8601 date. "
            "Returns {query, results: [{url, full_name, description, stargazers_count, "
            "language, updated_at, engagement_score, engagement_label, source_type}], error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_search_github,
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "max_results": {"type": "integer", "description": "Max results (default 5)"},
            "recency_iso": {"type": "string",  "description": "Optional ISO-8601 cutoff; adds pushed:>date qualifier"},
        },
    ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_communities.py -k github -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/tools/communities.py tests/tools/test_communities.py
git commit -m "feat: add search_github tool (GitHub Search API)"
```

---

## Task 6: communities register() smoke test

**Files:**
- Test: `tests/tools/test_communities.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
# ── register() ────────────────────────────────────────────────────────────────

def test_communities_register_exposes_all_three_tools():
    from unittest.mock import MagicMock
    from research.tools.communities import register
    registry = MagicMock()
    register(registry)
    names = [c.args[0].name for c in registry.register.call_args_list]
    assert {"search_hackernews", "search_polymarket", "search_github"} <= set(names)
```

- [ ] **Step 2: Run test**

Run: `pytest tests/tools/test_communities.py -k register -v`
Expected: PASS (register() already registers all three from Tasks 3–5). If FAIL, ensure all three `ToolDescriptor` blocks are inside `register()`.

- [ ] **Step 3: Commit**

```bash
git add tests/tools/test_communities.py
git commit -m "test: assert communities.register exposes all three tools"
```

---

## Task 7: web_search recency_days

**Files:**
- Modify: `research/tools/web.py` — `_handle_web_search` (around line 100–128) + `register()` web_search parameters (around line 333–340)
- Test: `tests/tools/test_web.py` (append)

**Interfaces:**
- Consumes: optional `recency_days` (int) from the workflow. Produces: unchanged result shape; passes `days=recency_days` to Tavily when set.

- [ ] **Step 1: Write the failing test** (append to `tests/tools/test_web.py`)

```python
async def test_web_search_forwards_recency_days_to_tavily(monkeypatch):
    from unittest.mock import MagicMock
    captured = {}
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    monkeypatch.setattr("research.tools.web._tavily_client", lambda: fake_client)
    from research.tools.web import _handle_web_search
    await _handle_web_search({"query": "ai", "recency_days": 30})
    kwargs = fake_client.search.call_args.kwargs
    assert kwargs["days"] == 30


async def test_web_search_omits_days_when_recency_unset(monkeypatch):
    from unittest.mock import MagicMock
    fake_client = MagicMock()
    fake_client.search.return_value = {"results": []}
    monkeypatch.setattr("research.tools.web._tavily_client", lambda: fake_client)
    from research.tools.web import _handle_web_search
    await _handle_web_search({"query": "ai"})
    kwargs = fake_client.search.call_args.kwargs
    assert "days" not in kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_web.py -k recency -v`
Expected: FAIL (no `days` kwarg forwarded).

- [ ] **Step 3: Write minimal implementation**

In `research/tools/web.py`, replace the body of `_handle_web_search` from the `client = _tavily_client()` line through the `response = client.search(...)` call. Find:

```python
        client = _tavily_client()
        # search_depth="basic" returns snippets only — fast and cheap.
        # The select_sources + fetch_url stages handle full content retrieval.
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=False,
            include_images=True,      # surface thumbnail images for the report
        )
```

Replace with:

```python
        client = _tavily_client()
        # search_depth="basic" returns snippets only — fast and cheap.
        # The select_sources + fetch_url stages handle full content retrieval.
        search_kwargs = dict(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=False,
            include_images=True,      # surface thumbnail images for the report
        )
        recency_days = args.get("recency_days")
        if recency_days:
            try:
                search_kwargs["days"] = int(recency_days)
            except (TypeError, ValueError):
                pass
        response = client.search(**search_kwargs)
```

Then add `recency_days` to the `web_search` tool parameters schema. Find the `parameters=` block for `web_search` in `register()`:

```python
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default 5)"},
        },
```

Replace with:

```python
        parameters={
            "query":       {"type": "string",  "description": "Search query string"},
            "max_results": {"type": "integer", "description": "Maximum number of results (default 5)"},
            "recency_days": {"type": "integer", "description": "Optional: restrict to results from the last N days (Tavily `days`)"},
        },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_web.py -v`
Expected: PASS (full file, no regressions)

- [ ] **Step 5: Commit**

```bash
git add research/tools/web.py tests/tools/test_web.py
git commit -m "feat: web_search forwards recency_days to Tavily"
```

---

## Task 8: social.py recency + engagement

**Files:**
- Modify: `research/tools/social.py` — `search_reddit` handler (lines ~109–136) + schema; `search_youtube_videos` handler (lines ~143–197) + schema; add `_reddit_time_filter` helper + engagement import.
- Test: `tests/tools/test_social.py` (append)

**Interfaces:**
- Consumes: optional `recency_days` (int). Produces: Reddit results gain `engagement_score`, `engagement_label`, `source_type`. `search_reddit` passes PRAW `time_filter`; `search_youtube_videos` passes Tavily `days`.

- [ ] **Step 1: Write the failing test** (append to `tests/tools/test_social.py`)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_social.py -k "recency or engagement or time_filter" -v`
Expected: FAIL (`_reddit_time_filter` not defined; `time_filter` not passed; engagement fields absent).

- [ ] **Step 3: Write minimal implementation**

In `research/tools/social.py`, add the engagement import near the other imports (after the `from armature...` lines):

```python
from research.tools.engagement import log_normalize, SCALES
```

Add the helper below the `_reddit_client` factory:

```python
def _reddit_time_filter(recency_days: Any) -> str:
    """Map a day count to the nearest PRAW time_filter bucket; 'all' when unset."""
    try:
        d = int(recency_days)
    except (TypeError, ValueError):
        return "all"
    if d <= 1:
        return "day"
    if d <= 7:
        return "week"
    if d <= 31:
        return "month"
    if d <= 366:
        return "year"
    return "all"
```

Replace the `search_reddit` loop and result dict. Find:

```python
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
```

Replace with:

```python
    try:
        results = []
        search_kwargs = dict(limit=max_results, sort=sort)
        recency_days = args.get("recency_days")
        if recency_days:
            search_kwargs["time_filter"] = _reddit_time_filter(recency_days)
        for submission in reddit.subreddit(subreddits).search(query, **search_kwargs):
            score = int(submission.score or 0)
            num_comments = int(submission.num_comments or 0)
            results.append({
                "url": f"https://reddit.com{submission.permalink}",
                "title": submission.title,
                "subreddit": submission.subreddit.display_name,
                "score": score,
                "num_comments": num_comments,
                "snippet": (submission.selftext or "")[:500],
                "created_utc": int(submission.created_utc),
                "engagement_score": round(max(
                    log_normalize(score, SCALES["reddit_score"]),
                    log_normalize(num_comments, SCALES["reddit_comments"]),
                ), 3),
                "engagement_label": f"▲ {score} · {num_comments} comments",
                "source_type": "reddit",
            })
        return {"query": query, "subreddits": subreddits, "results": results}
```

For `search_youtube_videos`, add recency to the Tavily call. Find:

```python
        try:
            response = client.search(
                query=f"{q} site:youtube.com",
                max_results=max_per_query,
                search_depth="basic",
                include_answer=False,
            )
```

Replace with:

```python
        try:
            search_kwargs = dict(
                query=f"{q} site:youtube.com",
                max_results=max_per_query,
                search_depth="basic",
                include_answer=False,
            )
            recency_days = args.get("recency_days")
            if recency_days:
                try:
                    search_kwargs["days"] = int(recency_days)
                except (TypeError, ValueError):
                    pass
            response = client.search(**search_kwargs)
```

Add the parameter to both schemas. In `register()`, for `search_reddit`'s `parameters=`, add:

```python
            "recency_days": {"type": "integer", "description": "Optional: restrict to posts from the last N days (PRAW time_filter)"},
```

And for `search_youtube_videos`'s `parameters=`, add:

```python
            "recency_days": {"type": "integer", "description": "Optional: restrict to videos indexed in the last N days (Tavily days)"},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_social.py -v`
Expected: PASS (full file, no regressions)

- [ ] **Step 5: Commit**

```bash
git add research/tools/social.py tests/tools/test_social.py
git commit -m "feat: reddit/youtube honor recency_days; reddit carries engagement metrics"
```

---

## Task 9: research-round.yaml wiring + structural test

**Files:**
- Modify: `workflows/research-round.yaml`
- Create: `tests/workflows/__init__.py`, `tests/workflows/test_structure.py`

**Interfaces:**
- Consumes: the three community tools + `parse_recency` tool + `recency_days`/`recency_iso` args from Tasks 2–8. Produces: `prepare_recency`, `run_hn_search`, `run_polymarket_search`, `run_github_search` stages; `select_sources` depends on all six searches.

- [ ] **Step 1: Write the failing test**

```python
# tests/workflows/__init__.py
```

```python
# tests/workflows/test_structure.py
"""Structural tests: research-round.yaml and research-analyst.yaml wiring for
recency + community sources."""
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROUND = ROOT / "workflows" / "research-round.yaml"
ANALYST = ROOT / "workflows" / "research-analyst.yaml"


def _load(path):
    return yaml.safe_load(path.read_text())


# ── research-round.yaml ────────────────────────────────────────────────────────

def test_round_registers_community_and_recency_modules():
    spec = _load(ROUND)
    modules = [t["module"] for t in spec["tools"]]
    assert "research.tools.communities" in modules
    assert "research.tools.recency" in modules


def test_round_declares_recency_input():
    names = [i["name"] for i in _load(ROUND)["contracts"]["inputs"]]
    assert "recency" in names


def test_round_has_prepare_recency_and_new_search_stages():
    ids = [s["id"] for s in _load(ROUND)["stages"]]
    for sid in ("prepare_recency", "run_hn_search", "run_polymarket_search", "run_github_search"):
        assert sid in ids, f"missing stage {sid}"


def test_select_sources_depends_on_new_stages():
    spec = _load(ROUND)
    stage = next(s for s in spec["stages"] if s["id"] == "select_sources")
    deps = stage["depends_on"]
    for sid in ("run_hn_search", "run_polymarket_search", "run_github_search"):
        assert sid in deps, f"select_sources missing dep {sid}"


# ── research-analyst.yaml ──────────────────────────────────────────────────────

def test_analyst_declares_recency_input():
    names = [i["name"] for i in _load(ANALYST)["contracts"]["inputs"]]
    assert "recency" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_structure.py -v`
Expected: FAIL (modules not registered, recency input absent, stages absent).

- [ ] **Step 3: Implement the YAML edits**

**Edit 3a — `research-round.yaml` tool modules.** Find:

```yaml
tools:
  - module: research.tools.web
  - module: research.tools.social
```

Replace with:

```yaml
tools:
  - module: research.tools.web
  - module: research.tools.social
  - module: research.tools.communities
  - module: research.tools.recency
```

**Edit 3b — declare `recency` input.** Find the round's `contracts.inputs` list (it contains `- name: topic`). Add `recency` alongside the existing optional inputs:

```yaml
    - name: recency   # Optional: recent-results window, e.g. "30d", "3d", "2mo". Unset = open-ended.
```

(Insert it immediately after the `- name: focus` line, or after `topic` if `focus` is absent in the round contract — match whatever optional-input line already exists.)

**Edit 3c — add `prepare_recency` + three search stages.** Insert these stages immediately *before* the existing `- id: run_searches` stage:

```yaml
  # ── 1b. Parse the recency window into structured values for downstream filters ──
  # Returns {days, phrase, iso_start} (zeros/empty when recency unset).
  - id: prepare_recency
    tool_call:
      name: parse_recency
      args:
        recency: "{{ recency | default('') }}"

  # ── 2a. Search Hacker News for developer sentiment ───────────────────────────
  # HN surfaces practitioner reactions and early signal that press coverage lags.
  - id: run_hn_search
    depends_on: [plan_round_queries, prepare_recency]
    fan_out: 4
    fan_in: list
    fail_as_value: true
    partition_source: "{{ plan_round_queries.queries }}"
    partition_key: query_item
    signature:
      input:
        query_item: Search query object with query string and intent
    tool_call:
      name: search_hackernews
      args:
        query: "{{ query_item.query }}"
        max_results: 3
        recency_iso: "{{ prepare_recency.iso_start }}"

  # ── 2b. Search Polymarket for crowd/money-backed forecasts ───────────────────
  # Prediction-market odds are a real-money signal of what people expect to happen.
  - id: run_polymarket_search
    depends_on: [plan_round_queries, prepare_recency]
    fan_out: 4
    fan_in: list
    fail_as_value: true
    partition_source: "{{ plan_round_queries.queries }}"
    partition_key: query_item
    signature:
      input:
        query_item: Search query object with query string and intent
    tool_call:
      name: search_polymarket
      args:
        query: "{{ query_item.query }}"
        max_results: 3
        recency_iso: "{{ prepare_recency.iso_start }}"

  # ── 2c. Search GitHub for projects and traction ─────────────────────────────
  # Stars and recent activity signal real adoption and where a field is moving.
  - id: run_github_search
    depends_on: [plan_round_queries, prepare_recency]
    fan_out: 4
    fan_in: list
    fail_as_value: true
    partition_source: "{{ plan_round_queries.queries }}"
    partition_key: query_item
    signature:
      input:
        query_item: Search query object with query string and intent
    tool_call:
      name: search_github
      args:
        query: "{{ query_item.query }}"
        max_results: 3
        recency_iso: "{{ prepare_recency.iso_start }}"

```

**Edit 3d — pass recency to the existing search stages.** In `- id: run_searches`, add `recency_days` to the `tool_call.args`:

```yaml
    tool_call:
      name: web_search
      args:
        query: "{{ search_item.query }}"
        max_results: 5
        recency_days: "{{ prepare_recency.days }}"
```

In `- id: run_reddit_search`, add to `tool_call.args`:

```yaml
        recency_days: "{{ prepare_recency.days }}"
```

In `- id: run_youtube_search`, add to `tool_call.args`:

```yaml
        recency_days: "{{ prepare_recency.days }}"
```

And add `prepare_recency` to each of those stages' `depends_on` (e.g. `depends_on: [plan_round_queries, prepare_recency]`).

**Edit 3e — update `select_sources` depends_on.** Find:

```yaml
  - id: select_sources
    depends_on: [run_searches, run_reddit_search, fetch_youtube_transcripts]
```

Replace with:

```yaml
  - id: select_sources
    depends_on: [run_searches, run_reddit_search, fetch_youtube_transcripts, run_hn_search, run_polymarket_search, run_github_search]
```

**Edit 3f — feed new sources into `select_sources` and `synthesize_round`.** In the `select_sources` stage's `signature.input` block, add three lines:

```yaml
        run_hn_search: Hacker News stories (each item has query + results array; empty means no matches)
        run_polymarket_search: Polymarket prediction markets (each item has query + results array; empty means no matches)
        run_github_search: GitHub repositories (each item has query + results array; empty means no matches)
```

In the `select_sources` prompt body (the Jinja template that already references `run_reddit_search` / `run_youtube_search`), append a paragraph:

```yaml
        Hacker News: {{ run_hn_search }} — developer sentiment and early signal; weight high-point, high-comment stories.
        Polymarket: {{ run_polymarket_search }} — real-money forecasts; weight high-volume markets and cite the odds.
        GitHub: {{ run_github_search }} — projects and traction; weight high-star repos with recent activity.
        Rank sources by real engagement signals (upvotes, stars, volume, views), not editor placement.
        {% if prepare_recency.phrase %}Constrain findings to content {{ prepare_recency.phrase }}.{% endif %}
```

Do the same for `synthesize_round`: add the three new sources to its `signature.input` and append the same engagement-weighting + recency-phrasing paragraph to its prompt body.

**Edit 3g — recency phrasing in `plan_round_queries`.** In the `plan_round_queries` prompt body, after the line that instructs generating queries, add:

```yaml
        {% if prepare_recency.phrase %}Append "{{ prepare_recency.phrase }}" to each search query so engines bias toward recent content.{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_structure.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Validate the spec loads and runs dry**

Run: `armature validate workflows/research-round.yaml && armature validate workflows/research-analyst.yaml`
Expected: both validate cleanly (no YAML/DAG errors).

- [ ] **Step 6: Commit**

```bash
git add workflows/research-round.yaml tests/workflows/__init__.py tests/workflows/test_structure.py
git commit -m "feat: wire recency + HN/Polymarket/GitHub into research-round workflow"
```

---

## Task 10: research-analyst.yaml recency input

**Files:**
- Modify: `workflows/research-analyst.yaml`
- Test: `tests/workflows/test_structure.py` (the analyst test from Task 9 already covers this)

- [ ] **Step 1: Run the test to confirm it's red**

Run: `pytest tests/workflows/test_structure.py::test_analyst_declares_recency_input -v`
Expected: FAIL (`recency` not in analyst inputs).

- [ ] **Step 2: Implement the edit**

In `workflows/research-analyst.yaml`, find the `contracts.inputs` list:

```yaml
  inputs:
    - name: topic          # The research question or subject area (required)
    - name: focus          # Optional: specific angle or constraint ("focus on regulatory implications")
```

Add `recency` after `focus`:

```yaml
    - name: recency        # Optional: recent-results window, e.g. "30d", "3d", "2mo", "1y". Unset = open-ended.
```

The round subagent (`deep_research_round`) shares the parent context (`isolated: false`), so the `recency` input flows into the round automatically — no explicit pass-through field is needed (it flows the same way `topic` and `focus` do).

Additionally, in the `write_report` stage prompt body, add a line so the final report frames recent findings:

```yaml
        {% if recency %}Recency window requested: {{ recency }}. Frame findings as recent and note the window in the report's scope note.{% endif %}
```

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/workflows/test_structure.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 4: Validate the spec**

Run: `armature validate workflows/research-analyst.yaml`
Expected: validates cleanly.

- [ ] **Step 5: Commit**

```bash
git add workflows/research-analyst.yaml
git commit -m "feat: declare recency input on research-analyst workflow"
```

---

## Task 11: Engagement badges in the HTML report

**Files:**
- Modify: `research/tools/reporting.py` — Sources panel loop (lines ~1149–1170) + CSS for `.sbadge`
- Test: `tests/tools/test_reporting.py` (create)

**Interfaces:**
- Consumes: source dicts that may carry an `engagement_label` string. Purely additive — sources without it render unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_reporting.py
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
        synthesized="# Findings\n\nbody",
        sources=_make_sources(),
    )
    assert "★ 90,000" in html
    assert "sbadge" in html


def test_sources_panel_omits_badge_when_absent():
    html = generate_visual_report(
        question="Test topic",
        synthesized="# Findings\n\nbody",
        sources=_make_sources(),
    )
    # The HN source (no engagement_label) must not introduce an empty badge span
    assert 'class="sbadge"></span>' not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_reporting.py -v`
Expected: FAIL (`sbadge` not in HTML; badge not rendered).

- [ ] **Step 3: Write minimal implementation**

In `research/tools/reporting.py`, in the sources panel loop, find:

```python
        for i, s in enumerate(sources, 1):
            url = s.get("url", "")
            title = html.escape(s.get("title", "") or url)
            domain = ""
            try:
                domain = urlparse(url).hostname or ""
                if domain.startswith("www."):
                    domain = domain[4:]
            except Exception:
                domain = url
            items.append(
                f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">'
                f'<span class="snum">{i}.</span>'
                f'<span>{title}</span>'
                f'<span class="sdomain">{html.escape(domain)}</span>'
                f'</a>'
            )
```

Replace with:

```python
        for i, s in enumerate(sources, 1):
            url = s.get("url", "")
            title = html.escape(s.get("title", "") or url)
            domain = ""
            try:
                domain = urlparse(url).hostname or ""
                if domain.startswith("www."):
                    domain = domain[4:]
            except Exception:
                domain = url
            label = s.get("engagement_label")
            label_html = (
                f'<span class="sbadge">{html.escape(label)}</span>'
                if label else ""
            )
            items.append(
                f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">'
                f'<span class="snum">{i}.</span>'
                f'<span>{title}</span>'
                f'{label_html}'
                f'<span class="sdomain">{html.escape(domain)}</span>'
                f'</a>'
            )
```

Add CSS for `.sbadge` in the stylesheet (alongside the existing `.sources-list .sdomain` rule, which uses doubled braces because the template is Python `.format()`-based):

```css
.sources-list .sbadge {{ color: var(--accent); font-size: 0.7rem; margin-left: 0.5rem; flex-shrink: 0; }}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_reporting.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS (no regressions across tools + workflow tests).

- [ ] **Step 6: Commit**

```bash
git add research/tools/reporting.py tests/tools/test_reporting.py
git commit -m "feat: render engagement badges in the report Sources panel"
```

---

## Task 12: End-to-end smoke run

**Files:** none (verification only)

- [ ] **Step 1: Validate both specs**

Run: `armature validate workflows/research-analyst.yaml && armature validate workflows/research-round.yaml`
Expected: both clean.

- [ ] **Step 2: Full test suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 3: Dry-run the workflow with recency**

Run: `armature run workflows/research-analyst.yaml --input "topic=GLM-5.2 reception" --input "recency=30d" --dry-run`
Expected: validates inputs and DAG without executing LLM calls; no errors about missing tools/inputs.

- [ ] **Step 4: Optional live run (if API keys set)**

Run: `armature run workflows/research-analyst.yaml --input "topic=GLM-5.2 reception" --input "recency=30d"`
Expected: a completed run whose report includes HN/GitHub/Polymarket source sections and engagement badges in the Sources panel; queries are phrased "in the last 30 days". Confirm sources degrade gracefully if `GITHUB_TOKEN` is unset (GitHub results may be empty/rate-limited, run continues).

- [ ] **Step 5: Commit any docs touch-ups**

If the README's usage/CLI section should mention `--input "recency=…"`, update `README.md` and commit:

```bash
git add README.md
git commit -m "docs: document the recency input option"
```

---

## Self-Review (already applied)

- **Spec coverage:** recency option → Tasks 2, 7, 8, 9, 10. Three new sources → Tasks 3–6, 9. Engagement weighting → Tasks 1, 3, 4, 5, 8, 11. Testing → embedded in every task + structural test (Task 9). All spec sections covered. Out-of-scope items (X/Twitter, competitive-intel) explicitly excluded.
- **Placeholder scan:** none — every code step contains complete, runnable code or exact find/replace edits.
- **Type consistency:** `parse_recency` returns `{days, phrase, iso_start}`; the `prepare_recency` stage and every downstream `{{ prepare_recency.days }}` / `{{ prepare_recency.iso_start }}` / `{{ prepare_recency.phrase }}` reference match. Tools accept `recency_days` (web/reddit/youtube) and `recency_iso` (HN/Polymarket/GitHub) consistently. `engagement_score` / `engagement_label` / `source_type` field names are identical across Tasks 3–5 and 8 and the reporting Task 11.