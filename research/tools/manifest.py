"""Source-manifest builder: joins selected URLs with raw tool results to attach
engagement labels for the HTML report's Sources panel.

Defensive by design — never raises; garbled inputs degrade to empty lists so a
tool failure can never crash a research run.
"""
from __future__ import annotations

import ast
import json
import urllib.parse
from typing import Any, Iterator


def _normalize_url(url: str) -> str:
    """Normalize a URL for matching/dedup: lowercase scheme + host, strip the
    trailing slash, and drop the fragment. The query string is preserved. Empty
    or non-string input returns "".
    """
    if not isinstance(url, str):
        return ""
    s = url.strip()
    if not s:
        return ""
    try:
        parsed = urllib.parse.urlsplit(s)
    except ValueError:
        return s.lower().rstrip("/")
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def _parse_list(value: Any) -> list:
    """Coerce a Jinja-rendered list arg back to a list. Actual lists pass through;
    strings are parsed via json.loads then ast.literal_eval; anything else (or
    parse failure, or a non-list JSON value) becomes []. Never raises.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            try:
                parsed = ast.literal_eval(s)
                return parsed if isinstance(parsed, list) else []
            except (ValueError, SyntaxError):
                return []
    return []


def _iter_engagement_items(payload: Any) -> Iterator[tuple[str, str]]:
    """Yield (url, engagement_label) pairs from a raw tool-result payload.

    Handles every shape produced by the search tools:
      - [{query, results: [{url, ..., engagement_label}]}, ...]  (web/hn/polymarket/github/reddit, per-query)
      - {query, results: [...]}                                   (reddit, single dict)
      - [{url, video_id, transcript}, ...]                        (youtube transcripts; no engagement_label -> skipped)
      - [{url, ..., engagement_label}, ...]                       (flat result items, no per-query wrapper)

    Only items with both a url and an engagement_label are yielded.
    """
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        items = entry.get("results")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    url = item.get("url")
                    label = item.get("engagement_label")
                    if url and label:
                        yield str(url), str(label)
        elif entry.get("url") and entry.get("engagement_label"):
            yield str(entry["url"]), str(entry["engagement_label"])


from armature.permissions.permissions import PermissionLevel
from armature.registry.registry import ToolDescriptor, ToolRegistry  # noqa: F401


async def _handle_build_source_manifest(args: dict[str, Any]) -> dict[str, Any]:
    """Join selected source URLs with raw search-tool results to build a source
    manifest carrying engagement labels for the HTML report. Accumulates across
    research rounds via `prior_manifest`. Never raises; garbled inputs degrade
    to empty lists and a tool failure can never crash a research run.
    """
    try:
        selected = _parse_list(args.get("selected_urls"))
        prior = _parse_list(args.get("prior_manifest"))

        # Build a normalized-URL -> engagement_label index from every raw result list.
        index: dict[str, str] = {}
        for key in ("web_results", "hn_results", "polymarket_results",
                    "github_results", "reddit_results", "youtube_results"):
            for url, label in _iter_engagement_items(_parse_list(args.get(key))):
                index.setdefault(_normalize_url(url), label)

        manifest: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Seed with the prior manifest (already enriched in previous rounds).
        for entry in prior:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            nkey = _normalize_url(url)
            if nkey in seen:
                continue
            seen.add(nkey)
            manifest.append({
                "url": url,
                "title": entry.get("title") or url,
                "engagement_label": entry.get("engagement_label"),
                "image": entry.get("image"),
            })

        # Append this round's selected URLs, enriching engagement from the index.
        for sel in selected:
            if not isinstance(sel, dict):
                continue
            url = sel.get("url")
            if not url:
                continue
            nkey = _normalize_url(url)
            if nkey in seen:
                continue
            seen.add(nkey)
            manifest.append({
                "url": url,
                "title": sel.get("title") or url,
                "engagement_label": index.get(nkey),
                "image": sel.get("image"),
            })

        return {"sources_manifest": manifest, "count": len(manifest), "error": None}
    except Exception as exc:
        # Best-effort fallback: selected URLs without engagement if anything blew up.
        fallback: list[dict[str, Any]] = []
        for sel in _parse_list(args.get("selected_urls")):
            if isinstance(sel, dict) and sel.get("url"):
                fallback.append({
                    "url": sel["url"],
                    "title": sel.get("title") or sel["url"],
                    "engagement_label": None,
                    "image": sel.get("image"),
                })
        return {"sources_manifest": fallback, "count": len(fallback), "error": str(exc)}


def register(registry: ToolRegistry) -> None:
    """Register the source-manifest builder tool."""
    registry.register(ToolDescriptor(
        name="build_source_manifest",
        description=(
            "Join selected source URLs with raw search-tool results to build a "
            "source manifest carrying engagement labels for the HTML report's "
            "Sources panel. Accumulates across research rounds via prior_manifest. "
            "Never raises; garbled inputs degrade to empty lists. Returns "
            "{sources_manifest: [{url, title, engagement_label?, image?}], count, error?}."
        ),
        permission=PermissionLevel.READ_ONLY,
        handler=_handle_build_source_manifest,
        parameters={
            "selected_urls":      {"type": "string", "description": "Selected URL objects [{url, title, image?}] (Jinja-rendered list)"},
            "prior_manifest":     {"type": "string", "description": "Carried-forward manifest from the prior round (empty on round 1)"},
            "web_results":        {"type": "string", "description": "Tavily web search results (no engagement labels)"},
            "hn_results":         {"type": "string", "description": "Hacker News search results"},
            "polymarket_results": {"type": "string", "description": "Polymarket search results"},
            "github_results":     {"type": "string", "description": "GitHub search results"},
            "reddit_results":     {"type": "string", "description": "Reddit search results"},
            "youtube_results":    {"type": "string", "description": "YouTube transcript results"},
        },
    ))