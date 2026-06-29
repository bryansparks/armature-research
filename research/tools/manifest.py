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