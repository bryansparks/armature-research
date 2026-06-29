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